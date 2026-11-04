#!/usr/bin/env python3
"""Drishti edge camera agent.

Runs at the SITE (home, mall, school, ICU ward) on any small computer on the same network as the cameras. It reads the
private RTSP / snapshot URL locally and PUSHES pictures out to your Drishti server over HTTPS. Nothing has to be opened
inbound: no VPN, no ngrok, no router port-forward, and the camera password never leaves the site.

  GATEWAY_URL=https://drishti.example.com  TENANT_ID=home_1_bangalore  EDGE_TOKEN=dre_...   (pair it in Admin > Connect a camera)
  CAMERAS="kitchen=rtsp://user:pw@192.168.1.50:8081/,icu_bed5=http://192.168.1.51/picture/1/current/"

An unchanged scene is not re-sent (it only sends a small keep-alive picture so the dashboard shows the camera as online);
a changed scene is sent at once. The server still applies its own idle gate, so this only saves bandwidth.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import signal
import threading
import time
from dataclasses import dataclass

import httpx
from PIL import Image

log = logging.getLogger("camera-agent")
GRID = (32, 24)                      # same 32x24 brightness-normalised grid the Drishti gateway uses
_stop = threading.Event()


def redact(text: str) -> str:
    return re.sub(r"//[^/@\s]+@", "//***@", text or "")


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    return "rtsp://" + u[6:] if re.match(r"^tcp://", u, re.I) else u        # ngrok shows RTSP tunnels as tcp://


def parse_cameras(spec: str) -> dict[str, str]:
    """'kitchen=rtsp://...,icu=http://...' -> {camera_id: url}. Camera ids must match [A-Za-z0-9_-]{1,40}."""
    out: dict[str, str] = {}
    for item in [x.strip() for x in (spec or "").split(",") if x.strip()]:
        cam, sep, url = item.partition("=")
        cam = cam.strip()
        if not sep or not url.strip() or not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", cam):
            log.error("ignoring malformed camera %r (want camera_id=url)", cam or item[:20])
            continue
        out[cam] = normalize_url(url)
    return out


def scene_signature(jpeg: bytes) -> list[int]:
    img = Image.open(io.BytesIO(jpeg)).convert("L").resize(GRID, Image.BOX)
    cells = list(img.tobytes())
    mean = sum(cells) / len(cells)
    return [round(c - mean) for c in cells]


def change_fraction(a: list[int], b: list[int], delta: int) -> float:
    return sum(1 for x, y in zip(a, b) if abs(x - y) > delta) / len(a)


@dataclass
class Gate:
    """Decide per camera whether a picture is worth sending now."""
    interval: float = 30.0              # how often a CHANGED scene is sent
    keepalive: float = 120.0            # an UNCHANGED scene is still shown on the dashboard this often
    fraction: float = 0.04
    delta: int = 14
    prev: list[int] | None = None
    last_sent: float = float("-inf")    # so the very first picture is always sent
    pending: bool = False               # a change was seen but is still waiting for the interval to pass

    def decide(self, jpeg: bytes, now: float) -> tuple[bool, bool]:
        """Returns (send, changed). A change that arrives too soon after the last send is held and sent as soon as the
        interval allows, never dropped."""
        sig = scene_signature(jpeg)
        if self.prev is None or change_fraction(self.prev, sig, self.delta) >= self.fraction:
            self.pending = True
        self.prev = sig
        since = now - self.last_sent
        send = (self.pending and since >= self.interval) or since >= self.keepalive
        changed = self.pending
        if send:
            self.last_sent, self.pending = now, False
        return send, changed


def grab(url: str, timeout: float = 10.0) -> bytes:
    """One JPEG from an http(s) snapshot URL or an RTSP/MJPEG/file source."""
    if url.lower().startswith(("http://", "https://")):
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            if resp.status_code == 200 and resp.headers.get("content-type", "").lower().startswith("image/"):
                return resp.read()
    import cv2  # imported lazily so the pure helpers above are testable without OpenCV
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;8000000")
    cap = cv2.VideoCapture(url)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("camera answered but no picture could be read (check path, username and password)")
        h, w = frame.shape[:2]
        if w > 1280:
            frame = cv2.resize(frame, (1280, round(h * 1280 / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise RuntimeError("could not encode the picture")
        return buf.tobytes()
    finally:
        cap.release()


def push(client: httpx.Client, gateway: str, tenant: str, token: str, camera: str, jpeg: bytes, changed: bool) -> int:
    resp = client.post(
        f"{gateway.rstrip('/')}/ingest/{tenant}", timeout=30, headers={"X-Edge-Token": token},
        files=[("file", ("frame.jpg", jpeg, "image/jpeg"))],
        data={"camera_id": camera, "sensors": json.dumps({"source": "edge-agent", "changed": changed})},
    )
    return resp.status_code


def run_camera(cam: str, url: str, cfg: dict) -> None:
    gate = Gate(cfg["interval"], cfg["keepalive"], cfg["fraction"])
    fails, warned = 0, False
    with httpx.Client() as client:
        while not _stop.is_set():
            wait = cfg["poll"]
            try:
                jpeg = grab(url)
                send, changed = gate.decide(jpeg, time.time())
                if send:
                    code = push(client, cfg["gateway"], cfg["tenant"], cfg["token"], cam, jpeg, changed)
                    if code == 200:
                        fails, warned = 0, False
                        log.info("%s: sent (%s, %d KB)", cam, "changed" if changed else "keep-alive", len(jpeg) // 1024)
                    elif code in (401, 403):
                        raise RuntimeError("the server rejected the site token: pair this site again (Admin > Connect a camera)")
                    else:
                        raise RuntimeError(f"server answered HTTP {code}")
                else:
                    fails, warned = 0, False
            except Exception as exc:  # noqa: BLE001 - one bad camera must never stop the others
                fails += 1
                wait = min(300.0, cfg["poll"] * 2 ** min(fails, 5))     # back off while a link is down
                if not warned:
                    warned = True
                    log.error("%s: %s (retrying, quietly, every %ds)", cam, redact(str(exc)), wait)
            _stop.wait(wait)


def load_config(env=os.environ) -> dict:
    cams = parse_cameras(env.get("CAMERAS", "") or (f"{env.get('CAMERA_ID', 'cam_1')}={env['CAMERA_URL']}" if env.get("CAMERA_URL") else ""))
    missing = [k for k in ("GATEWAY_URL", "TENANT_ID", "EDGE_TOKEN") if not env.get(k)]
    if missing or not cams:
        raise SystemExit("Set " + ", ".join(missing + ([] if cams else ["CAMERAS (camera_id=url,...)"])) +
                         " - see edge/camera-agent/README.md")
    return {"cams": cams, "gateway": env["GATEWAY_URL"], "tenant": env["TENANT_ID"], "token": env["EDGE_TOKEN"],
            "interval": float(env.get("INTERVAL_SEC", 30)), "keepalive": float(env.get("KEEPALIVE_SEC", 120)),
            "fraction": float(env.get("CHANGE_FRACTION", 0.04)), "poll": float(env.get("POLL_SEC", 10))}


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="read one picture from every camera, print the size, and exit")
    args = ap.parse_args()
    cfg = load_config()
    if args.check:
        bad = 0
        for cam, url in cfg["cams"].items():
            try:
                print(f"OK   {cam}: {len(grab(url)) // 1024} KB picture from {redact(url)}")
            except Exception as exc:  # noqa: BLE001
                bad += 1
                print(f"FAIL {cam}: {redact(str(exc))}")
        raise SystemExit(1 if bad else 0)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _stop.set())
    log.info("edge camera agent -> %s as tenant %s, %d camera(s)", cfg["gateway"], cfg["tenant"], len(cfg["cams"]))
    threads = [threading.Thread(target=run_camera, args=(c, u, cfg), daemon=True, name=c) for c, u in cfg["cams"].items()]
    for t in threads:
        t.start()
    while not _stop.is_set():
        _stop.wait(1)


if __name__ == "__main__":
    main()
