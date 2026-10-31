from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo


def now_local() -> datetime:
    """Current time in the deployment's timezone (TZ env var, set by docker compose)."""
    return datetime.now(ZoneInfo(os.environ.get("TZ", "Asia/Kolkata")))


def normalize_bbox(bbox, width: int | None = None, height: int | None = None):
    """Return [x1,y1,x2,y2] in 0..1, or None if unusable.

    SAM-style APIs may return pixel coordinates; if any value is > 1.5 we divide
    by the image size.
    """
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if max(x1, y1, x2, y2) > 1.5:
        if not width or not height:
            return None
        x1, x2 = x1 / width, x2 / width
        y1, y2 = y1 / height, y2 / height
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
    return [round(clamp(x1), 4), round(clamp(y1), 4), round(clamp(x2), 4), round(clamp(y2), 4)]


def clean_objects(raw, width: int | None = None, height: int | None = None, limit: int = 100) -> list[dict]:
    """Validate / normalise whatever a segmentation model returned."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for o in raw[:limit]:
        if not isinstance(o, dict):
            continue
        label = str(o.get("label", "")).strip().lower().replace(" ", "_")[:40]
        if not label:
            continue
        item = {"label": label}
        bb = normalize_bbox(o.get("bbox"), width, height) if o.get("bbox") is not None else None
        if bb:
            item["bbox"] = bb
        try:
            item["score"] = round(float(o.get("score", 1.0)), 3)
        except (TypeError, ValueError):
            item["score"] = 1.0
        if o.get("state") is not None:
            item["state"] = str(o["state"]).lower()[:20]
        out.append(item)
    return out


def jsonable(value):
    """Best-effort conversion (neo4j temporal types, datetimes, bytes...)."""
    return json.loads(json.dumps(value, default=str))
