"""OCR agent: segmented -> perceived.

Reads text on ICU monitors, mall signs and school notices. LOW_COST_MODE: only runs for verticals in
OCR_VERTICALS and only when a text-bearing object is in view (or the vertical is ICU); static scenes reuse
the previous result. Uses a vision model through the Meta Model API (PaddleOCR is not bundled: it adds
~1 GB to the image; swap `read_text` if you want a local engine).
"""
from __future__ import annotations

import io
import json

from PIL import Image

from drishti import bus, llm
from drishti.config import settings
from drishti.log import get_logger
from drishti.rules import labels_of, norm_label

log = get_logger("ocr-reader")
TEXT_LABELS = {"monitor", "gate_sign", "sign", "notice", "display", "screen", "board"}
OCR_TTL = 3600


def read_text(jpeg: bytes, r) -> list[str]:
    prompt = (
        "Read all legible text on screens, monitors, signs and notices in this image, exactly as written "
        '(keep numbers and words like ALARM). Return JSON only: {"texts":["..."]}. Empty list if none.'
    )
    out = llm.chat_json(settings.ocr_model, [{"role": "user", "content": [{"type": "text", "text": prompt}, llm.image_part(jpeg)]}],
                        max_tokens=200, r=r, reasoning=False)
    texts = out.get("texts")
    return [str(t)[:160] for t in texts][:20] if isinstance(texts, list) else []


def zoom_image(p: dict) -> bytes | None:
    """'Lean in': crop the monitors / signs found by segmentation out of the hi-res frame (stacked in one picture, max 3).
    No text-bearing object (or no hi-res copy) -> the hi-res whole frame, else None and the caller uses the normal frame."""
    hires = bus.get_hires(p["frame_id"])
    if hires is None:
        return None
    boxes = sorted((o for o in p.get("objects") or [] if norm_label(o.get("label", "")) in TEXT_LABELS and o.get("bbox")),
                   key=lambda o: -(o["bbox"][2] - o["bbox"][0]) * (o["bbox"][3] - o["bbox"][1]))[:3]
    if not boxes:
        return hires
    img = Image.open(io.BytesIO(hires)).convert("RGB")
    w, h = img.size
    crops = []
    for o in boxes:
        x1, y1, x2, y2 = o["bbox"]
        pad_x, pad_y = (x2 - x1) * 0.12, (y2 - y1) * 0.12
        c = img.crop((int(max(0, x1 - pad_x) * w), int(max(0, y1 - pad_y) * h), int(min(1, x2 + pad_x) * w), int(min(1, y2 + pad_y) * h)))
        if c.width < 8 or c.height < 8:
            continue
        scale = min(2.0, 900 / c.width)                                    # small crops are enlarged, never beyond 900 px wide
        crops.append(c.resize((max(8, int(c.width * scale)), max(8, int(c.height * scale)))))
    if not crops:
        return hires
    sheet = Image.new("RGB", (max(c.width for c in crops), sum(c.height for c in crops) + 6 * (len(crops) - 1)), (0, 0, 0))
    y = 0
    for c in crops:
        sheet.paste(c, (0, y))
        y += c.height + 6
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=88)
    return out.getvalue()


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
            jpeg = zoom_image(p) or bus.get_frame(p["frame_id"])
            if jpeg is not None:
                try:
                    texts = read_text(jpeg, r)
                    r.set(key, json.dumps(texts), ex=OCR_TTL)
                except llm.CallBudgetExceeded:
                    texts = json.loads(cached) if cached else []
                    log.warning("daily Meta API call budget exhausted - using cached OCR for %s/%s", tid, cam)
    bus.publish("perceived", tid, {**p, "ocr_texts": texts}, r)


if __name__ == "__main__":
    bus.consume("segmented", "ocr-reader", handle)
