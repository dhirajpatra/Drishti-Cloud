"""Perception agent: raw_frames -> segmented.

Segments each frame with SAM 3.1 (Meta Model API) into labelled objects with boxes.
LOW_COST_MODE: a frame the gateway marked `static` reuses the last segmentation of that camera.
MOCK_MODE: objects come from the simulator's `_sim` hint (no API, no key).
"""
from __future__ import annotations

import json

from drishti import bus, llm, simulate
from drishti.config import settings
from drishti.log import get_logger
from drishti.verticals import VERTICALS

log = get_logger("sam3-segmenter")
SEG_TTL = 3600


def clamp_box(b) -> list[float] | None:
    try:
        x1, y1, x2, y2 = (min(1.0, max(0.0, float(v))) for v in b)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def clean(objects) -> list[dict]:
    out = []
    for o in objects if isinstance(objects, list) else []:
        if not isinstance(o, dict) or not isinstance(o.get("label"), str):
            continue
        box = clamp_box(o.get("bbox"))
        if box is None:
            continue
        try:
            conf = min(1.0, max(0.0, float(o.get("confidence", 0.8))))
        except (TypeError, ValueError):
            conf = 0.8
        out.append({"label": o["label"].strip().lower()[:40], "bbox": box, "confidence": round(conf, 3)})
    return out[:200]


def mock_segment(p: dict) -> list[dict]:
    sim = (p.get("sensors") or {}).get("_sim") or {}
    if isinstance(sim.get("objects"), list):
        return clean(simulate.expand(sim["objects"]))
    return clean([{"label": "person", "bbox": [0.35, 0.2, 0.65, 0.9], "confidence": 0.9}])


def sam_segment(jpeg: bytes, vertical: str, r) -> list[dict]:
    labels = ", ".join(VERTICALS[vertical]["sam_labels"])
    prompt = (
        f"Segment all visible instances of: {labels}. Only include objects that are really visible. "
        'Return JSON only: {"objects":[{"label":"<name>","bbox":[x1,y1,x2,y2],"confidence":0.0-1.0}]} '
        "with bbox as fractions of image width/height (0-1)."
    )
    msgs = [{"role": "user", "content": [{"type": "text", "text": prompt}, llm.image_part(jpeg)]}]
    try:
        out = llm.chat_json(settings.sam_model, msgs, max_tokens=900, r=r)
    except (llm.NoOutputText, ValueError) as e:
        # SAM answered but not with parseable JSON text: fall back to the Spark vision model so the pipeline keeps flowing.
        if settings.spark_model == settings.sam_model:
            raise
        log.warning("%s gave no usable JSON (%s) - falling back to %s", settings.sam_model, e, settings.spark_model)
        out = llm.chat_json(settings.spark_model, msgs, max_tokens=900, r=r)
    return clean(out.get("objects"))


def handle(p: dict, r) -> None:
    tid, cam = p["tenant_id"], p.get("camera_id", "cam_1")
    cache_key = f"seg:{tid}:{cam}"
    cached = r.get(cache_key)
    source = "mock" if settings.mock_mode else "sam"

    if p.get("static") and cached and settings.low_cost_mode:
        objects, source = json.loads(cached), "cache"
    elif settings.mock_mode:
        objects = mock_segment(p)
    else:
        jpeg = bus.get_frame(p["frame_id"])
        if jpeg is None:
            log.warning("frame %s expired before segmentation - dropped", p["frame_id"])
            return
        objects = sam_segment(jpeg, p["vertical"], r)
    if source != "cache":
        r.set(cache_key, json.dumps(objects), ex=SEG_TTL)

    log.info("%s/%s segmented %d objects (%s)", tid, cam, len(objects), source)
    bus.publish("segmented", tid, {**p, "objects": objects, "seg_source": source}, r)


if __name__ == "__main__":
    bus.consume("raw_frames", "sam3-segmenter", handle)
