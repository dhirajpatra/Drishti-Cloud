"""Feed ingestor: samples RTSP streams / MP4 files / phone-camera URLs and posts frames to the gateway.

RTSP_FEEDS="mall_1:mall_security:cam_1=rtsp://user:pw@10.0.0.5/stream1,home_1:home_seniors:kitchen=/app/feeds/kitchen.mp4"
Each feed is tagged tenant_id:vertical:camera_id. Posting through the gateway (not straight to Redis) keeps ONE
validation path: auth, rate limit, perceptual-hash dedupe (LOW_COST_MODE) and the raw_frames stream.
Sampling every SAMPLE_INTERVAL_SEC (default 30). MP4 files advance by that interval each sample and loop.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;8000000")

import cv2  # noqa: E402
import httpx  # noqa: E402
import redis  # noqa: E402

from drishti import bus  # noqa: E402
from drishti.config import settings  # noqa: E402
from drishti.log import get_logger  # noqa: E402
from drishti.verticals import VERTICALS  # noqa: E402

log = get_logger("feed-ingestor")
TOKEN = os.environ.get("ADMIN_TOKEN", "")
_pos_ms: dict[str, float] = {}
_warned: set[str] = set()
TRIGGER_BLOCK_SEC = 5
TRIGGER_SOCKET_TIMEOUT_SEC = TRIGGER_BLOCK_SEC + 5


def parse_feeds(spec: str) -> list[dict]:
    feeds = []
    for item in [x.strip() for x in spec.split(",") if x.strip()]:
        tag, sep, url = item.partition("=")
        parts = tag.split(":")
        if not sep or len(parts) != 3 or parts[1] not in VERTICALS or not url:
            log.error("ignoring malformed feed %r (want tenant:vertical:camera=url)", item.split("=")[0])
            continue
        feeds.append({"tenant": parts[0], "vertical": parts[1], "camera": parts[2], "url": url})
    return feeds


def grab_snapshot(url: str) -> bytes | None:
    """http(s) URL that answers with ONE jpeg (motionEye /picture/<n>/current/, ESP32-CAM /capture, phone apps).
    Returns None for anything else (e.g. an MJPEG multipart stream) so the caller can use OpenCV instead."""
    with httpx.stream("GET", url, timeout=10, follow_redirects=True) as resp:
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code != 200 or not ctype.startswith("image/"):
            return None
        return resp.read()


def grab(feed: dict) -> bytes | None:
    if feed["url"].lower().startswith(("http://", "https://")):
        try:
            data = grab_snapshot(feed["url"])
        except httpx.HTTPError as exc:
            raise RuntimeError(f"snapshot request failed: {exc}") from exc
        if data:
            return data
    url, is_file = feed["url"], not feed["url"].lower().startswith(("rtsp://", "rtsps://", "http://", "https://"))
    key = f"{feed['tenant']}:{feed['camera']}"
    cap = cv2.VideoCapture(url)
    try:
        if not cap.isOpened():
            raise RuntimeError("cannot open stream")
        if is_file:
            cap.set(cv2.CAP_PROP_POS_MSEC, _pos_ms.get(key, 0.0))
        frame = None
        for _ in range(1 if is_file else 5):  # live streams: skip buffered frames, keep the latest
            ok, f = cap.read()
            if ok:
                frame = f
        if frame is None and is_file:  # reached the end: loop
            _pos_ms[key] = 0.0
            cap.set(cv2.CAP_PROP_POS_MSEC, 0.0)
            _, frame = cap.read()
        if frame is None:
            raise RuntimeError("no frame")
        if is_file:
            _pos_ms[key] = _pos_ms.get(key, 0.0) + settings.sample_interval_sec * 1000
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None
    finally:
        cap.release()


def grab_burst(feed: dict, n: int, spacing: float) -> list[bytes]:
    """n frames `spacing` seconds apart, oldest first (a guard watches for a few seconds before judging)."""
    frames: list[bytes] = []
    for i in range(n):
        if i:
            time.sleep(spacing)
        try:
            f = grab(feed)
        except Exception:  # noqa: BLE001 - a missed frame must not lose the ones we have
            f = None
        if f:
            frames.append(f)
    return frames


def push(feed: dict, sensors: dict | None = None, burst: int = 1) -> None:
    key = f"{feed['tenant']}/{feed['camera']}"
    try:
        if burst > 1:
            frames = grab_burst(feed, burst, settings.burst_spacing_sec)
            jpeg, earlier = (frames[-1], frames[:-1]) if frames else (None, [])
        else:
            jpeg, earlier = grab(feed), []
        if jpeg is None:
            return
        resp = httpx.post(
            f"{settings.gateway_url}/ingest/{feed['tenant']}", timeout=30,
            headers={"X-Admin-Token": TOKEN} if TOKEN else {},
            files=[("file", ("frame.jpg", jpeg, "image/jpeg"))] + [("burst", (f"b{i}.jpg", b, "image/jpeg")) for i, b in enumerate(earlier)],
            data={"camera_id": feed["camera"], "sensors": json.dumps(sensors or {"source": "rtsp"})},
        )
        if resp.status_code == 200:
            _warned.discard(key)
            body = resp.json()
            log.info("%s -> queued (static=%s, change=%s)", key, body.get("static"), body.get("change"))
        elif key not in _warned:
            _warned.add(key)
            log.error("%s rejected by gateway: HTTP %s %s", key, resp.status_code, resp.text[:120])
    except Exception as exc:  # noqa: BLE001
        if key not in _warned:  # log a flapping camera once, not every 30 s
            _warned.add(key)
            log.error("%s failed: %s", key, exc)


def all_feeds(env_feeds: list[dict]) -> dict[tuple[str, str], dict]:
    merged = {(f["tenant"], f["camera"]): f for f in env_feeds}          # .env feeds ...
    merged.update({(f["tenant"], f["camera"]): f for f in bus.registered_feeds()})   # ... overridden by dashboard-registered ones
    return merged


def handle_trigger(raw: str, env_feeds: list[dict]) -> bool:
    """A motionEye web hook fired: grab that camera's frame NOW and analyse it (motion=true bypasses the idle gate)."""
    t = json.loads(raw)
    feed = all_feeds(env_feeds).get((t["tenant"], t["camera"]))
    if feed is None:
        log.warning("motion hook for %s/%s but no such feed (camera id must match RTSP_FEEDS / the Camera sources panel)", t["tenant"], t["camera"])
        return False
    log.info("%s/%s motion hook -> analysing now", t["tenant"], t["camera"])
    push(feed, {"source": "motioneye_hook", "pir": True}, burst=settings.burst_frames)
    return True


def trigger_loop(env_feeds: list[dict]) -> None:
    r = redis.Redis.from_url(
        settings.redis_uri,
        decode_responses=True,
        health_check_interval=30,
        socket_timeout=TRIGGER_SOCKET_TIMEOUT_SEC,
    )
    while not bus.stopping():
        try:
            item = r.blpop("trigger:feed", timeout=TRIGGER_BLOCK_SEC)
            if item:
                handle_trigger(item[1], env_feeds)
        except Exception:  # noqa: BLE001
            log.exception("motion trigger failed")
            bus._stop.wait(2)


def main() -> None:
    feeds = parse_feeds(os.environ.get("RTSP_FEEDS", ""))
    if not feeds:
        log.info("RTSP_FEEDS is empty - idle (use the edge simulator or POST /ingest/{tenant} instead)")
    else:
        log.info("sampling %d feed(s) every %ss", len(feeds), settings.sample_interval_sec)

    pool = ThreadPoolExecutor(max_workers=16)
    threading.Thread(target=trigger_loop, args=(feeds,), daemon=True, name="motion-triggers").start()

    def tick() -> None:
        try:
            extra = bus.registered_feeds()                       # cameras added from the dashboard (override .env on a clash)
        except Exception:  # noqa: BLE001
            log.exception("could not read dashboard-registered feeds")
            extra = []
        merged = {(f["tenant"], f["camera"]): f for f in feeds}
        merged.update({(f["tenant"], f["camera"]): f for f in extra})
        list(pool.map(push, merged.values()))

    bus.run_periodic("feed-ingestor", settings.sample_interval_sec, tick)


if __name__ == "__main__":
    main()
