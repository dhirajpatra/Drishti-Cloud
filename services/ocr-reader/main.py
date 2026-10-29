"""OCR agent: segmented -> perceived.

Reads text on ICU monitors, mall signs and school notices. LOW_COST_MODE: only runs for verticals in
OCR_VERTICALS and only when a text-bearing object is in view (or the vertical is ICU); static scenes reuse
the previous result. Uses a vision model through the Meta Model API (PaddleOCR is not bundled: it adds
~1 GB to the image; swap `read_text` if you want a local engine).
"""
from __future__ import annotations

import json

from drishti import bus, llm
from drishti.config import settings
from drishti.log import get_logger
from drishti.rules import labels_of

log = get_logger("ocr-reader")
TEXT_LABELS = {"monitor", "gate_sign", "sign", "notice", "display", "screen", "board"}
OCR_TTL = 3600


def read_text(jpeg: bytes, r) -> list[str]:
    prompt = (
        "Read all legible text on screens, monitors, signs and notices in this image, exactly as written "
        '(keep numbers and words like ALARM). Return JSON only: {"texts":["..."]}. Empty list if none.'
    )
    out = llm.chat_json(settings.ocr_model, [{"role": "user", "content": [{"type": "text", "text": prompt}, llm.image_part(jpeg)]}],
                        max_tokens=400, r=r)
    texts = out.get("texts")
    return [str(t)[:160] for t in texts][:20] if isinstance(texts, list) else []


def wanted(p: dict) -> bool:
    if p["vertical"] not in settings.ocr_verticals:
        return False
    return p["vertical"] == "icu_hospital" or bool(TEXT_LABELS & set(labels_of(p.get("objects") or [])))


def handle(p: dict, r) -> None:
    tid, cam = p["tenant_id"], p.get("camera_id", "cam_1")
    key = f"ocr:{tid}:{cam}"
    texts: list[str] = []
    if settings.mock_mode:
        sim = (p.get("sensors") or {}).get("_sim") or {}
        texts = [str(t)[:160] for t in sim.get("ocr", [])][:20] if isinstance(sim.get("ocr"), list) else []
    elif wanted(p):
        cached = r.get(key)
        if p.get("static") and cached and settings.low_cost_mode:
            texts = json.loads(cached)
        else:
            jpeg = bus.get_frame(p["frame_id"])
            if jpeg is not None:
                texts = read_text(jpeg, r)
                r.set(key, json.dumps(texts), ex=OCR_TTL)
    bus.publish("perceived", tid, {**p, "ocr_texts": texts}, r)


if __name__ == "__main__":
    bus.consume("segmented", "ocr-reader", handle)
