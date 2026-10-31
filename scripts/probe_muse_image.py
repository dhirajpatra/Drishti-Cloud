"""See what Muse Image really returns on your Meta key (nothing in Drishti calls it yet - this shows the reply shape).

    docker compose exec -T gateway python - < scripts/probe_muse_image.py
    docker compose exec -T gateway python - muse-image-1.0 "A calm illustrated card: an elderly person resting in a living room" < scripts/probe_muse_image.py

Muse Image generates / edits / composes images ($0.01 per image). It does NOT analyse or segment pictures, so detection stays with
SAM 3.1 and Muse Spark. A sensible future use is a privacy-preserving *illustration* for WhatsApp alerts (no real faces of children
or patients leave the building) - never "enhancing" evidence, because a generator can invent people and objects.
"""
import base64
import json
import os
import sys

import requests

from drishti.config import settings

model = sys.argv[1] if len(sys.argv) > 1 else os.getenv("IMAGE_MODEL", "muse-image-1.0")
prompt = sys.argv[2] if len(sys.argv) > 2 else "A simple friendly illustration of a house with a green check mark, flat style"
url = settings.llama_api_url.rstrip("/")
url = url if url.endswith("/responses") else url + "/responses"
resp = requests.post(url, headers={"Authorization": f"Bearer {settings.llama_api_key}"}, timeout=180,
                     json={"model": model, "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]})
print("HTTP", resp.status_code)


def clip(o):
    if isinstance(o, dict):
        return {k: clip(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clip(v) for v in o[:6]]
    if isinstance(o, str) and len(o) > 200:
        return o[:80] + f"...<{len(o)} chars>"
    return o


try:
    data = resp.json()
    print(json.dumps(clip(data), indent=2)[:5000])
    for item in data.get("output") or []:               # save the first base64 image found, if any
        for part in (item.get("content") or []) if isinstance(item, dict) else []:
            b64 = part.get("image_base64") or part.get("b64_json") or (part.get("image") or {}).get("data") if isinstance(part, dict) else None
            if b64:
                open("/tmp/muse_image_probe.png", "wb").write(base64.b64decode(b64))
                print("saved /tmp/muse_image_probe.png inside the container")
except Exception:  # noqa: BLE001
    print(resp.text[:1500])
