"""Check your Baseten hackathon key from inside the stack (the key is never printed).

    docker compose exec -T gateway python - < scripts/probe_baseten.py
    docker compose exec -T gateway python - meta-models/muse-spark-open-1026 < scripts/probe_baseten.py

1. GET /v1/models      -> is the key accepted, and is your model slug in the list?
2. a tiny text call    -> does the model answer, and what does the reply look like?
3. a tiny image call   -> does it accept OpenAI-style image_url parts (what Drishti sends)?
"""
import io
import json
import sys

import requests
from PIL import Image, ImageDraw

from drishti import llm
from drishti.config import settings

slug = sys.argv[1] if len(sys.argv) > 1 else "meta-models/muse-spark-open-1026"
base = settings.baseten_api_url.rstrip("/")
H = {"Authorization": f"Bearer {settings.baseten_api_key}", "Content-Type": "application/json"}
if not settings.baseten_api_key:
    sys.exit("BASETEN_API_KEY is empty in .env (docker compose up -d --force-recreate after adding it)")

r = requests.get(base + "/models", headers=H, timeout=30)
print("GET /models ->", r.status_code)
try:
    ids = [m.get("id") for m in r.json().get("data", [])]
    print(f"{len(ids)} models; contains {slug!r}: {slug in ids}")
    print("muse-like ids:", [i for i in ids if i and "muse" in i.lower()])
except Exception:  # noqa: BLE001
    print(r.text[:500])


def call(content, label):
    resp = requests.post(base + "/chat/completions", headers=H, timeout=90,
                         json={"model": slug, "max_tokens": 400, "messages": [{"role": "user", "content": content}]})
    print(f"\n--- {label}: HTTP {resp.status_code}")
    print(json.dumps(resp.json(), indent=2)[:2500] if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:500])


call('Reply with exactly this JSON and nothing else: {"ok": true}', "text")
img = Image.new("RGB", (320, 240), (30, 40, 60))
ImageDraw.Draw(img).rectangle([100, 80, 220, 220], fill=(220, 180, 60))
buf = io.BytesIO(); img.save(buf, "JPEG")
call(llm.to_openai_messages([{"role": "user", "content": [
    {"type": "text", "text": 'What colour is the big rectangle? Reply as JSON {"colour": "..."}'}, llm.image_part(buf.getvalue())]}])[0]["content"], "image")
