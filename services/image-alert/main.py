"""Image-alert agent: decided -> proofed.

Produces the visual proof attached to an alert: the frame with the involved objects circled in red, a level
banner and the reason. Drawn deterministically with Pillow on purpose - proof must show what the camera
actually saw, never a generated picture.
"""
from __future__ import annotations

import io
import textwrap
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from drishti import bus
from drishti.log import get_logger
from drishti.rules import norm_label

log = get_logger("image-alert")
COLORS = {"CRITICAL": (239, 68, 68), "ALERT": (245, 158, 11)}


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def render(jpeg: bytes, p: dict) -> bytes:
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = img.size
    color = COLORS.get(p["level"], (239, 68, 68))
    d = ImageDraw.Draw(img, "RGBA")
    involved = {norm_label(x) for x in p.get("involved", [])}
    f = _font(max(11, w // 45))

    for o in p.get("objects") or []:
        x1, y1, x2, y2 = (o["bbox"][0] * w, o["bbox"][1] * h, o["bbox"][2] * w, o["bbox"][3] * h)
        hot = norm_label(o["label"]) in involved
        if hot:
            d.rectangle([x1 - 4, y1 - 4, x2 + 4, y2 + 4], outline=color + (255,), width=4)
            d.ellipse([x1 - 10, y1 - 10, x2 + 10, y2 + 10], outline=color + (200,), width=2)
            d.text((x1, max(0, y1 - 20)), o["label"], fill=color + (255,), font=f)
        elif len(p["objects"]) <= 12:
            d.rectangle([x1, y1, x2, y2], outline=(120, 200, 160, 160), width=1)

    bar = max(26, h // 14)
    d.rectangle([0, 0, w, bar], fill=color + (230,))
    stamp = datetime.now().strftime("%H:%M:%S")
    d.text((8, bar // 2 - f.size // 2 if hasattr(f, "size") else 4),
           f"{p['level']}  {p.get('rule') or 'alert'}   {p['tenant_id']} / {p.get('camera_id', '')}   {stamp}",
           fill=(255, 255, 255, 255), font=f)
    lines = textwrap.wrap(p.get("reason", ""), width=max(20, w // 8))[:3]
    if lines:
        box_h = (f.size if hasattr(f, "size") else 11) * (len(lines) + 1)
        d.rectangle([0, h - box_h - 8, w, h], fill=(0, 0, 0, 170))
        for i, line in enumerate(lines):
            d.text((8, h - box_h - 4 + i * (f.size if hasattr(f, "size") else 11) + 2), line, fill=(255, 255, 255, 255), font=f)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue()


def handle(p: dict, r) -> None:
    tid = p["tenant_id"]
    has_proof = False
    if p["level"] != "NORMAL":
        jpeg = bus.get_frame(p["frame_id"])
        if jpeg is None:
            log.warning("frame %s expired - alert goes out without image proof", p["frame_id"])
        else:
            bus.put_proof(p["frame_id"], render(jpeg, p))
            has_proof = True
    bus.publish("proofed", tid, {**p, "has_proof": has_proof}, r)


if __name__ == "__main__":
    bus.consume("decided", "image-alert", handle)
