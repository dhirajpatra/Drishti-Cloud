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

from drishti import bus, siteconfig  # noqa: E402
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
    return siteconfig.parse_feeds(spec)


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
            _pos_ms[key] = _pos_ms.get(key, 0.0) + feed.get("interval", settings.sample_interval_sec) * 1000
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


def _status(feed: dict, ok: bool, error: str) -> None:
    """Last result per camera, shown on the Admin page (credentials never included)."""
    try:
        r = bus.redis_client()
        k = f"feedstat:{feed['tenant']}:{feed['camera']}"
        r.hset(k, mapping={"ok": "1" if ok else "0", "error": siteconfig.mask_url(error), "ts": bus.utcnow_iso()})
        r.expire(k, 7 * 86400)
    except Exception:  # noqa: BLE001
        pass


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
            _status(feed, True, "")
            body = resp.json()
            log.info("%s -> queued (static=%s, change=%s)", key, body.get("static"), body.get("change"))
        elif key not in _warned:
            _warned.add(key)
            log.error("%s rejected by gateway: HTTP %s %s", key, resp.status_code, resp.text[:120])
        if resp.status_code != 200:
            _status(feed, False, f"gateway HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        _status(feed, False, str(exc)[:200])
        if key not in _warned:  # log a flapping camera once, not every 30 s
            _warned.add(key)
            log.error("%s failed: %s", key, exc)


def all_feeds(env_spec: str | None = None) -> dict[tuple[str, str], dict]:
    """Cameras to sample: .env RTSP_FEEDS plus everything saved in the Admin page (Postgres), the latter winning."""
    spec = os.environ.get("RTSP_FEEDS", "") if env_spec is None else env_spec
    return {(f["tenant"], f["camera"]): f for f in siteconfig.all_feeds(spec)}


def test_connection(raw: str) -> None:
    """Admin 'Test connection': grab one frame with the saved (or just-typed) settings and report back through Redis."""
    req = json.loads(raw)
    r = bus.redis_client()
    out: dict = {"ok": False, "error": "", "bytes": 0}
    try:
        feed = {"tenant": req["tenant"], "camera": "_test", "url": req["url"], "interval": 30}
        data = grab(feed)
        out.update(ok=bool(data), bytes=len(data or b""), error="" if data else "no picture returned")
        if data:
            bus.redis_bin().set(f"camtest:img:{req['id']}", data, ex=120)
    except Exception as exc:  # noqa: BLE001
        out["error"] = siteconfig.mask_url(str(exc))[:300]
    r.set(f"camtest:res:{req['id']}", json.dumps(out), ex=120)


def handle_trigger(raw: str, env_feeds: list[dict] | None = None) -> bool:
    """A motionEye web hook fired: grab that camera's frame NOW and analyse it (motion=true bypasses the idle gate)."""
    t = json.loads(raw)
    feed = all_feeds().get((t["tenant"], t["camera"]))
    if feed is None:
        log.warning("motion hook for %s/%s but no such feed (camera id must match RTSP_FEEDS / the Camera sources panel)", t["tenant"], t["camera"])
        return False
    log.info("%s/%s motion hook -> analysing now", t["tenant"], t["camera"])
    push(feed, {"source": "motioneye_hook", "pir": True}, burst=feed.get("burst", settings.burst_frames))
    return True


def trigger_loop(env_feeds: list[dict] | None = None) -> None:
    # dedicated client: its socket timeout must exceed the BLPOP block time or every idle wait raises a timeout
    r = redis.Redis.from_url(settings.redis_uri, decode_responses=True, socket_timeout=TRIGGER_SOCKET_TIMEOUT_SEC)
    while not bus.stopping():
        try:
            item = r.blpop(["camtest:req", "trigger:feed"], timeout=TRIGGER_BLOCK_SEC)
            if item:
                (test_connection if item[0] == "camtest:req" else handle_trigger)(item[1])
        except Exception:  # noqa: BLE001
            log.exception("trigger failed")
            bus._stop.wait(2)


TICK_SEC = 5
_due: dict[tuple[str, str], float] = {}


def due_feeds(feeds: dict[tuple[str, str], dict], now: float) -> list[dict]:
    """Each camera has its own sampling interval (Admin page); a new or changed camera is sampled on the next tick."""
    for k in [k for k in _due if k not in feeds]:
        del _due[k]
    out = []
    for k, f in feeds.items():
        if now >= _due.get(k, 0):
            _due[k] = now + f.get("interval", settings.sample_interval_sec)
            out.append(f)
    return out


def main() -> None:
    log.info("feed-ingestor up: cameras come from RTSP_FEEDS and from the Admin page; each is sampled at its own interval")
    pool = ThreadPoolExecutor(max_workers=16)
    threading.Thread(target=trigger_loop, daemon=True, name="motion-triggers").start()

    def tick() -> None:
        try:
            feeds = all_feeds()
        except Exception:  # noqa: BLE001
            log.exception("could not read camera settings")
            return
        list(pool.map(push, due_feeds(feeds, time.time())))

    bus.run_periodic("feed-ingestor", TICK_SEC, tick)


if __name__ == "__main__":
    main()
