"""Offline simulation helpers (MOCK_MODE only).

The edge simulator attaches a `_sim` hint to the sensors JSON describing the scene it drew:
    {"objects": [{"label": "person", "count": 3}, {"label": "bag", "bbox": [x1,y1,x2,y2]}],
     "ocr": ["HR 132 ALARM"], "hour": 2, "elapsed": {"unattended_bag": 150}}
The gateway strips `_sim` outside MOCK_MODE, so a real deployment never trusts these hints.
"""
from __future__ import annotations

import math

MAX_OBJECTS = 150


def expand(objects: list[dict]) -> list[dict]:
    """Expand {"label", "count"} entries into individual objects with normalised boxes (x1,y1,x2,y2 in 0..1)."""
    out: list[dict] = []
    for o in objects or []:
        if not isinstance(o, dict) or not o.get("label"):
            continue
        label = str(o["label"])[:40]
        bbox = o.get("bbox")
        count = o.get("count", 1)
        count = max(1, min(int(count) if isinstance(count, (int, float)) else 1, MAX_OBJECTS))
        if count == 1 and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            out.append({"label": label, "bbox": [float(x) for x in bbox], "confidence": 0.9})
            continue
        cols = max(1, math.ceil(math.sqrt(count * 1.5)))
        rows = math.ceil(count / cols)
        w, h = 0.9 / cols, 0.9 / rows
        for i in range(count):
            c, r = i % cols, i // cols
            x1, y1 = 0.05 + c * w, 0.05 + r * h
            out.append({"label": label, "bbox": [x1 + w * 0.1, y1 + h * 0.1, x1 + w * 0.9, y1 + h * 0.9], "confidence": 0.9})
    return out
