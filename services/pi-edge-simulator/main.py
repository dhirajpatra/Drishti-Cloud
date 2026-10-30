"""Pi 5 edge simulator: lets the whole cloud run on a laptop with no hardware and no API key.

For each tenant it cycles through realistic scenarios (normal, quiet/static, and each vertical's hazards),
draws a synthetic frame, posts it with a sensor JSON exactly like pi_edge.py, then polls the actuation
endpoint and "performs" the commands (speaker / OLED / ISD1820) by logging them.
Scenario hints travel in sensors["_sim"]; the gateway only honours them in MOCK_MODE.
"""
from __future__ import annotations

import io
import json
import os
import time

import httpx
from PIL import Image, ImageDraw

from drishti import bus, simulate
from drishti.config import settings
from drishti.log import get_logger

log = get_logger("pi-edge-simulator")
GATEWAY = settings.gateway_url
TOKEN = os.environ.get("ADMIN_TOKEN", "")
INTERVAL = int(os.environ.get("SIM_INTERVAL_SEC", "20"))
TENANTS = [t.strip() for t in os.environ.get("SIM_TENANTS", "mall_1,home_1_bangalore,school_1,icu_ward_1").split(",") if t.strip()]

BG = {"mall_security": (58, 52, 70), "home_seniors": (70, 62, 52), "school_children": (50, 66, 76), "icu_hospital": (60, 74, 70)}
COLOR = {"person": (90, 160, 255), "child": (120, 220, 255), "bag": (255, 160, 60), "fallen_person": (255, 90, 90),
         "stove": (230, 120, 60), "door": (160, 120, 80), "bed": (150, 150, 200), "monitor": (60, 220, 120),
         "staff": (130, 255, 160), "badge": (255, 230, 90), "gate_open": (255, 120, 120), "corridor": (90, 90, 110),
         "patient": (200, 160, 255), "restricted_door": (255, 80, 80), "violence": (255, 40, 40)}

P = lambda n: {"label": "person", "count": n}  # noqa: E731

# (name, sensors, sim-hint)  -- order matters: mostly normal, hazards interleaved
SCENARIOS: dict[str, list[tuple[str, dict, dict]]] = {
    "mall_security": [
        ("shoppers", {"pir": True, "temp": 31}, {"objects": [P(4)]}),
        ("quiet_corner", {"pir": False}, {"objects": [P(1)]}),
        ("unattended_bag", {"pir": True}, {"objects": [{"label": "bag", "bbox": [0.4, 0.55, 0.55, 0.8]}], "elapsed": {"unattended_bag": 150}}),
        ("shoppers", {"pir": True, "temp": 31}, {"objects": [P(5)]}),
        ("crowd_surge", {"pir": True}, {"objects": [P(64)]}),
        ("fight", {"pir": True}, {"objects": [P(2), {"label": "violence", "bbox": [0.3, 0.3, 0.7, 0.9]}]}),
    ],
    "home_seniors": [
        ("morning_routine", {"pir": True, "temp": 28}, {"objects": [P(1), {"label": "stove", "bbox": [0.6, 0.4, 0.85, 0.7]}], "hour": 8}),
        ("quiet_room", {"pir": False, "us_door": 30}, {"objects": [], "hour": 11}),
        ("door_open_night", {"pir": False, "us_door": 150}, {"objects": [{"label": "door", "bbox": [0.1, 0.1, 0.35, 0.95]}], "hour": 2}),
        ("morning_walk_habit", {"pir": True, "us_door": 150}, {"objects": [{"label": "door", "bbox": [0.1, 0.1, 0.35, 0.95]}, P(1)], "hour": 4}),
        ("no_motion_2h", {"pir": False}, {"objects": [], "hour": 11, "elapsed": {"no_motion": 7500}}),
        ("afternoon_nap_habit", {"pir": False}, {"objects": [], "hour": 15, "elapsed": {"no_motion": 7500}}),
        ("fall", {"pir": True}, {"objects": [{"label": "fallen_person", "bbox": [0.25, 0.6, 0.7, 0.9]}], "hour": 16}),
        ("call_for_help", {"pir": True, "audio_transcript": "help me please"}, {"objects": [P(1)], "hour": 17}),
    ],
    "school_children": [
        ("class_in_session", {"pir": True}, {"objects": [{"label": "child", "count": 6}, {"label": "staff", "count": 1}], "hour": 10}),
        ("gate_open_class", {"pir": True}, {"objects": [{"label": "gate_open", "bbox": [0.2, 0.2, 0.8, 0.9]}], "hour": 11}),
        ("assembly_gate_habit", {"pir": True}, {"objects": [{"label": "gate_open", "bbox": [0.2, 0.2, 0.8, 0.9]}], "hour": 9}),
        ("stranger_near_kids", {"pir": True}, {"objects": [{"label": "child", "count": 3}, {"label": "person", "bbox": [0.7, 0.2, 0.9, 0.9]}], "hour": 11}),
    ],
    "icu_hospital": [
        ("stable_patient", {"pir": True, "temp": 25}, {"objects": [{"label": "patient", "bbox": [0.3, 0.4, 0.7, 0.7]}, {"label": "bed", "bbox": [0.2, 0.35, 0.8, 0.8]},
                                                                   {"label": "staff", "bbox": [0.82, 0.2, 0.95, 0.9]}], "ocr": ["HR 78", "SpO2 97"]}),
        ("monitor_alarm", {"pir": True}, {"objects": [{"label": "monitor", "bbox": [0.05, 0.1, 0.3, 0.4]}, {"label": "patient", "bbox": [0.4, 0.5, 0.8, 0.75]}], "ocr": ["HR 132 ALARM"]}),
        ("stable_patient", {"pir": True, "temp": 25}, {"objects": [{"label": "patient", "bbox": [0.3, 0.4, 0.7, 0.7]}, {"label": "bed", "bbox": [0.2, 0.35, 0.8, 0.8]},
                                                                   {"label": "staff", "bbox": [0.82, 0.2, 0.95, 0.9]}], "ocr": ["HR 80", "SpO2 96"]}),
        ("patient_on_floor", {"pir": True}, {"objects": [{"label": "fallen_person", "bbox": [0.2, 0.7, 0.75, 0.95]}, {"label": "bed", "bbox": [0.2, 0.2, 0.8, 0.6]}]}),
    ],
}


def draw(vertical: str, hint: dict) -> bytes:
    img = Image.new("RGB", (640, 480), BG.get(vertical, (60, 60, 60)))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 380, 640, 480], fill=tuple(int(c * 0.8) for c in BG.get(vertical, (60, 60, 60))))
    for o in simulate.expand(hint.get("objects", [])):
        x1, y1, x2, y2 = o["bbox"][0] * 640, o["bbox"][1] * 480, o["bbox"][2] * 640, o["bbox"][3] * 480
        d.rectangle([x1, y1, x2, y2], fill=COLOR.get(o["label"], (180, 180, 180)))
        if (x2 - x1) > 50:
            d.text((x1 + 4, y1 + 4), o["label"], fill=(0, 0, 0))
    for i, t in enumerate(hint.get("ocr", [])):
        d.text((20, 20 + 14 * i), t, fill=(120, 255, 160))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return out.getvalue()


def headers() -> dict:
    return {"X-Admin-Token": TOKEN} if TOKEN else {}


def perform(tenant: str, c: httpx.Client) -> None:
    """Behave like the Pi: fetch queued commands and 'actuate' them."""
    resp = c.get(f"{GATEWAY}/edge/{tenant}/actuate", headers=headers())
    for cmd in resp.json().get("commands", []):
        pl = cmd.get("payload", {})
        if cmd["kind"] == "speak":
            log.warning("[PI %s] SPEAKER (PAM8403 + 5W woofer): \"%s\"", tenant, pl.get("text"))
        elif cmd["kind"] == "oled":
            log.warning("[PI %s] OLED SSD1306 shows: %s", tenant, pl.get("text"))
        elif cmd["kind"] == "isd1820":
            log.warning("[PI %s] ISD1820 loud alarm for %ss", tenant, pl.get("duration_sec"))


def main() -> None:
    state = {t: 0 for t in TENANTS}
    verticals: dict[str, str] = {}
    log.info("simulating %s every %ss", TENANTS, INTERVAL)

    with httpx.Client(timeout=30) as c:
        warned_mode = False
        blocked = False

        def tick() -> None:
            nonlocal warned_mode, blocked
            if not warned_mode:
                mode = c.get(f"{GATEWAY}/health").json()
                if not mode.get("mock_mode") and os.environ.get("SIM_ALLOW_LIVE", "").lower() not in ("1", "true", "yes"):
                    blocked = True
                    log.error("gateway is using the REAL models (MOCK_MODE=false): the simulator is paused so synthetic "
                              "frames do not burn API quota. Remove COMPOSE_PROFILES=sim from .env (or set SIM_ALLOW_LIVE=true "
                              "to send them anyway).")
                warned_mode = True
            if blocked:
                return
            for t in TENANTS:
                if t not in verticals:
                    r = c.get(f"{GATEWAY}/tenants/{t}", headers=headers())
                    if r.status_code != 200:
                        log.warning("tenant %s not found (HTTP %s) - skipped", t, r.status_code)
                        continue
                    verticals[t] = r.json()["vertical"]
                v = verticals[t]
                name, sensors, hint = SCENARIOS[v][state[t] % len(SCENARIOS[v])]
                state[t] += 1
                resp = c.post(
                    f"{GATEWAY}/ingest/{t}", headers=headers(),
                    files={"file": ("frame.jpg", draw(v, hint), "image/jpeg")},
                    data={"camera_id": "cam_1", "sensors": json.dumps({**sensors, "_sim": hint})},
                )
                log.info("%s scenario=%-20s -> HTTP %s %s", t, name, resp.status_code, resp.text[:80])
                perform(t, c)

        bus.run_periodic("pi-edge-simulator", INTERVAL, tick)


if __name__ == "__main__":
    main()
