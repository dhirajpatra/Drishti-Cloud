"""Print the RAW reply of a Meta Model API model for a tiny test image - use it to see what a model really returns.

    docker compose exec -T gateway python - sam-3.1 < scripts/probe_meta_api.py
    docker compose exec -T gateway python - muse-spark-1.3 < scripts/probe_meta_api.py
"""
import io
import json
import sys

import requests
from PIL import Image, ImageDraw

from drishti import llm
from drishti.config import settings

model = sys.argv[1] if len(sys.argv) > 1 else settings.sam_model
img = Image.new("RGB", (320, 240), (30, 40, 60))
ImageDraw.Draw(img).rectangle([100, 80, 220, 220], fill=(220, 180, 60))
buf = io.BytesIO(); img.save(buf, "JPEG")
msgs = [{"role": "user", "content": [{"type": "text", "text": 'List visible objects as JSON {"objects":[{"label":"","bbox":[x1,y1,x2,y2]}]}'},
                                    llm.image_part(buf.getvalue())]}]
url = settings.llama_api_url.rstrip("/")
url = url if url.endswith("/responses") else url + "/responses"
resp = requests.post(url, headers={"Authorization": f"Bearer {settings.llama_api_key}"},
                     json={"model": model, "input": llm.to_responses_input(msgs), "max_output_tokens": 600}, timeout=90)
print("HTTP", resp.status_code)
print(json.dumps(resp.json(), indent=2)[:6000])
