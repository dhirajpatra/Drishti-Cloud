"""End-to-end smoke test against a RUNNING stack (one injected hazard per vertical).

    make smoke                                   # runs inside the gateway container
    GATEWAY_URL=http://localhost:8000 python scripts/smoke.py   # from the host (needs pillow + httpx)

Uses the simulator's `_sim` hints, so it only proves the pipeline in MOCK_MODE. With a real API key the
hints are ignored and the synthetic picture is judged by the real models (it will correctly say NORMAL).
"""
import io
import json
import os
import sys
import time

import httpx
from PIL import Image, ImageDraw

GW = os.environ.get("GATEWAY_URL", "http://localhost:8000")
H = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]} if os.environ.get("ADMIN_TOKEN") else {}

CASES = [
    ("mall_1", "unattended bag", {"pir": True, "_sim": {"objects": [{"label": "bag", "bbox": [0.4, 0.55, 0.55, 0.8]}], "elapsed": {"unattended_bag": 150}}}, "ALERT"),
    ("home_1_bangalore", "senior fall", {"pir": True, "_sim": {"objects": [{"label": "fallen_person", "bbox": [0.2, 0.6, 0.7, 0.9]}]}}, "CRITICAL"),
    ("school_1", "stranger near children", {"pir": True, "_sim": {"hour": 11, "objects": [{"label": "child", "count": 3}, {"label": "person", "bbox": [0.7, 0.2, 0.9, 0.9]}]}}, "CRITICAL"),
    ("icu_ward_1", "monitor alarm", {"pir": True, "_sim": {"ocr": ["HR 132 ALARM"], "objects": [{"label": "monitor", "bbox": [0.05, 0.1, 0.3, 0.4]}]}}, "CRITICAL"),
]


def jpeg(seed: int) -> bytes:
    img = Image.new("RGB", (640, 480), (40 + seed * 20 % 120, 60, 90))
    ImageDraw.Draw(img).rectangle([100 + seed * 30, 100, 300 + seed * 30, 380], fill=(200, 160, 60))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def main() -> int:
    c = httpx.Client(timeout=30, headers=H)
    health = c.get(f"{GW}/health").json()
    print("health:", health)
    if not health.get("mock_mode"):
        print("NOTE: not in MOCK_MODE - _sim hints are ignored, expect NORMAL results.")
    failures = 0
    for i, (tenant, name, sensors, want) in enumerate(CASES):
        r = c.post(f"{GW}/ingest/{tenant}", files={"file": ("f.jpg", jpeg(i), "image/jpeg")},
                   data={"sensors": json.dumps(sensors), "force": "true"})
        if r.status_code != 200:
            print(f"FAIL {tenant:18} {name}: ingest HTTP {r.status_code} {r.text[:100]}")
            failures += 1
            continue
        frame_id, ev = r.json()["frame_id"], None
        for _ in range(60):  # pipeline has 6 hops; allow 60 s
            ev = next((e for e in c.get(f"{GW}/tenants/{tenant}/events?limit=50").json() if e["frame_id"] == frame_id), None)
            if ev:
                break
            time.sleep(1)
        if not ev:
            print(f"FAIL {tenant:18} {name}: no event after 60 s - check `docker compose logs`")
            failures += 1
        elif ev["level"] != want:
            print(f"FAIL {tenant:18} {name}: level {ev['level']} (wanted {want}) - {ev['reason']}")
            failures += 1
        else:
            alerts = [a for a in c.get(f"{GW}/tenants/{tenant}/alerts?limit=20").json() if a["frame_id"] == frame_id]
            note = f"alert {alerts[0]['id'][:8]} actions={alerts[0]['actions']}" if alerts else "alert deduplicated by cooldown"
            print(f"PASS {tenant:18} {name}: {ev['level']} | {ev['reason']} | {note}")
    print("SMOKE", "FAILED" if failures else "OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
