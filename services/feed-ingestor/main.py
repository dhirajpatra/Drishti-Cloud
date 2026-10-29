"""Feed ingestor: samples RTSP streams / MP4 files / phone-camera URLs and posts frames to the gateway.

RTSP_FEEDS="mall_1:mall_security:cam_1=rtsp://user:pw@10.0.0.5/stream1,home_1:home_seniors:kitchen=/app/feeds/kitchen.mp4"
Each feed is tagged tenant_id:vertical:camera_id. Posting through the gateway (not straight to Redis) keeps ONE
validation path: auth, rate limit, perceptual-hash dedupe (LOW_COST_MODE) and the raw_frames stream.
Sampling every SAMPLE_INTERVAL_SEC (default 30). MP4 files advance by that interval each sample and loop.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;8000000")

import cv2  # noqa: E402
import httpx  # noqa: E402

from drishti import bus  # noqa: E402
from drishti.config import settings  # noqa: E402
from drishti.log import get_logger  # noqa: E402
from drishti.verticals import VERTICALS  # noqa: E402

log = get_logger("feed-ingestor")
TOKEN = os.environ.get("ADMIN_TOKEN", "")
_pos_ms: dict[str, float] = {}
_warned: set[str] = set()


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


def grab(feed: dict) -> bytes | None:
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


def push(feed: dict) -> None:
    key = f"{feed['tenant']}/{feed['camera']}"
    try:
        jpeg = grab(feed)
        if jpeg is None:
            return
        resp = httpx.post(
            f"{settings.gateway_url}/ingest/{feed['tenant']}", timeout=30,
            headers={"X-Admin-Token": TOKEN} if TOKEN else {},
            files={"file": ("frame.jpg", jpeg, "image/jpeg")},
            data={"camera_id": feed["camera"], "sensors": json.dumps({"source": "rtsp"})},
        )
        if resp.status_code == 200:
            _warned.discard(key)
            log.info("%s -> queued (static=%s)", key, resp.json().get("static"))
        elif key not in _warned:
            _warned.add(key)
            log.error("%s rejected by gateway: HTTP %s %s", key, resp.status_code, resp.text[:120])
    except Exception as exc:  # noqa: BLE001
        if key not in _warned:  # log a flapping camera once, not every 30 s
            _warned.add(key)
            log.error("%s failed: %s", key, exc)


def main() -> None:
    feeds = parse_feeds(os.environ.get("RTSP_FEEDS", ""))
    if not feeds:
        log.info("RTSP_FEEDS is empty - idle (use the edge simulator or POST /ingest/{tenant} instead)")
    else:
        log.info("sampling %d feed(s) every %ss", len(feeds), settings.sample_interval_sec)

    pool = ThreadPoolExecutor(max_workers=max(1, min(16, len(feeds))))

    def tick() -> None:
        list(pool.map(push, feeds))

    bus.run_periodic("feed-ingestor", settings.sample_interval_sec, tick)


if __name__ == "__main__":
    main()
