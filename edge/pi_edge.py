#!/usr/bin/env python3
"""Drishti edge client for Raspberry Pi 5.

    python pi_edge.py --tenant_id home_1_bangalore --vertical home_seniors --gateway http://CLOUD_IP:8000 --key <tenant api key>

Loop: read sensors -> grab a 640x480 frame -> skip if PIR is quiet and the scene hash is unchanged (saves API cost)
-> POST /ingest/{tenant} -> poll /edge/{tenant}/actuate and perform speaker / OLED / ISD1820 commands.

NOT TESTED ON REAL HARDWARE (written from the pin map in the README). Pins are BCM numbers, override with flags.
Wiring reminders: HC-SR04 ECHO must go through the 1k/2k divider (5V -> 3.3V) or it will damage the Pi.
Speech uses `espeak-ng` -> 3.5 mm jack -> PAM8403 -> 2" 5 W woofer. Speech-to-text from the USB mic is optional:
write the transcript to --transcript-file and it is sent as sensors.audio_transcript.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger("pi_edge")


def dhash(img: Image.Image, size: int = 16) -> int:
    g = img.convert("L").resize((size + 1, size))
    px, bits = list(g.getdata()), 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (px[row * (size + 1) + col] > px[row * (size + 1) + col + 1])
    return bits


class Hardware:
    """Every device is optional: a missing library or wire just yields None / a log line."""

    def __init__(self, a: argparse.Namespace) -> None:
        self.a, self.pir, self.us_front, self.us_door, self.ir, self.isd, self.cam, self.oled = a, None, None, None, None, None, None, None
        try:
            from gpiozero import DigitalInputDevice, DigitalOutputDevice, DistanceSensor, MotionSensor
            self.pir = MotionSensor(a.pin_pir)
            self.us_front = DistanceSensor(echo=a.pin_us1_echo, trigger=a.pin_us1_trig, max_distance=3)
            self.us_door = DistanceSensor(echo=a.pin_us2_echo, trigger=a.pin_us2_trig, max_distance=3)
            self.ir = DigitalInputDevice(a.pin_ir, pull_up=True)
            self.isd = DigitalOutputDevice(a.pin_isd_play)
        except Exception as exc:  # noqa: BLE001
            log.warning("gpio unavailable: %s", exc)
        try:
            from picamera2 import Picamera2
            self.cam = Picamera2()
            self.cam.configure(self.cam.create_still_configuration(main={"size": (640, 480)}))
            self.cam.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("camera unavailable: %s", exc)
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            self.oled = ssd1306(i2c(port=1, address=0x3C))
        except Exception as exc:  # noqa: BLE001
            log.warning("oled unavailable: %s", exc)

    def sensors(self) -> dict:
        s: dict = {}
        if self.pir is not None:
            s["pir"] = bool(self.pir.motion_detected)
        if self.us_front is not None:
            s["us_front"] = round(self.us_front.distance * 100)
        if self.us_door is not None:
            s["us_door"] = round(self.us_door.distance * 100)
        if self.ir is not None:
            s["ir_obstacle"] = not bool(self.ir.value)
        try:
            import adafruit_dht, board  # noqa: E401
            dht = adafruit_dht.DHT11(board.D4)
            s["temp"] = dht.temperature
        except Exception:  # noqa: BLE001
            pass
        tf = Path(self.a.transcript_file) if self.a.transcript_file else None
        if tf and tf.exists():
            s["audio_transcript"] = tf.read_text().strip()[-200:]
            tf.write_text("")
        return s

    def frame(self) -> Image.Image | None:
        if self.cam is None:
            return None
        return Image.fromarray(self.cam.capture_array()).convert("RGB")

    def speak(self, text: str, volume: float) -> None:
        log.info("SPEAK: %s", text)
        subprocess.run(["espeak-ng", "-a", str(int(200 * volume)), "-s", "140", text], check=False)

    def show(self, text: str) -> None:
        log.info("OLED: %s", text)
        if self.oled is None:
            return
        from luma.core.render import canvas
        with canvas(self.oled) as draw:
            draw.text((4, 20), text, fill="white")

    def siren(self, seconds: float) -> None:
        log.info("ISD1820 play %ss", seconds)
        if self.isd is not None:
            self.isd.on()
            time.sleep(min(seconds, 15))
            self.isd.off()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant_id", required=True)
    ap.add_argument("--vertical", default="home_seniors", help="informational; the cloud knows the tenant's vertical")
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--key", default="", help="tenant API key (X-Tenant-Key); required when the cloud has AUTH_REQUIRED=true")
    ap.add_argument("--camera_id", default="cam_1")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--hash-distance", type=int, default=6)
    ap.add_argument("--transcript-file", default="")
    for name, pin in [("pir", 17), ("us1_trig", 23), ("us1_echo", 24), ("us2_trig", 5), ("us2_echo", 6), ("ir", 27), ("isd_play", 22)]:
        ap.add_argument(f"--pin_{name}", type=int, default=pin)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    hw, last_hash, headers = Hardware(a), None, ({"X-Tenant-Key": a.key} if a.key else {})
    base = a.gateway.rstrip("/")
    with httpx.Client(timeout=30, headers=headers) as c:
        next_t = 0.0
        while True:
            try:
                if time.time() >= next_t:
                    next_t = time.time() + a.interval
                    img, sensors = hw.frame(), hw.sensors()
                    if img is not None:
                        h = dhash(img)
                        quiet = sensors.get("pir") is False and last_hash is not None and bin(h ^ last_hash).count("1") <= a.hash_distance
                        last_hash = h
                        if not quiet:
                            buf = io.BytesIO()
                            img.save(buf, "JPEG", quality=80)
                            r = c.post(f"{base}/ingest/{a.tenant_id}", files={"file": ("f.jpg", buf.getvalue(), "image/jpeg")},
                                       data={"camera_id": a.camera_id, "sensors": json.dumps(sensors)})
                            log.info("ingest -> %s %s", r.status_code, r.text[:80])
                for cmd in c.get(f"{base}/edge/{a.tenant_id}/actuate").json().get("commands", []):
                    pl = cmd.get("payload", {})
                    if cmd["kind"] == "speak":
                        hw.speak(pl.get("text", ""), float(pl.get("volume", 0.7)))
                    elif cmd["kind"] == "oled":
                        hw.show(pl.get("text", ""))
                    elif cmd["kind"] == "isd1820":
                        hw.siren(float(pl.get("duration_sec", 5)))
            except Exception as exc:  # noqa: BLE001 - never die on a flaky network
                log.warning("loop error: %s", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
