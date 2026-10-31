"""Fridge inventory agent: looks INSIDE a fridge camera and keeps the stock list of everyday food up to date.

For every camera that has tracked items (milk, eggs, bread, butter, cheese, vegetables ... added from the dashboard) it takes
the camera's newest frame, asks the vision model which of those items it can see and how many, stores the counts and raises
one "Need to buy: ..." notification. Cost control: it only asks the model when the picture changed (a door opened, shopping
put away) or every INVENTORY_REFRESH_SEC, never more often than INVENTORY_SCAN_SEC. The dashboard's "Scan now" skips the wait.

Honest limits: it only updates items the model is sure about, a closed door / dark / blocked view changes nothing, and items
hidden behind others can read as missing. It tells you what to check, it does not replace looking.
"""
from __future__ import annotations

import io
import json
import os
import time

from PIL import Image

from drishti import bus, db, inventory, llm
from drishti.config import settings
from drishti.log import get_logger

log = get_logger("fridge-inventory-agent")
SCAN_SEC = int(os.environ.get("INVENTORY_SCAN_SEC", "600"))            # minimum gap between scans of one camera
REFRESH_SEC = int(os.environ.get("INVENTORY_REFRESH_SEC", "21600"))    # rescan even an unchanged picture this often
CHANGE = float(os.environ.get("INVENTORY_CHANGE", "6"))                # mean grey-level difference that counts as "changed"

PROMPT = """This is a camera picture of the inside of a refrigerator or kitchen shelf.
First decide whether the INSIDE of the fridge (shelves with food) is actually visible. Closed door, very dark, blocked or
unclear = false.
Then, for each of these items, count what you can clearly see: {names}.
Counting: eggs = individual eggs; milk / curd / juice = cartons, packets or bottles; bread = loaves or packs; butter / cheese = packs;
vegetables / fruits = separate bags, bunches or pieces. 0 means you can see the shelves and it is not there.
If part of the fridge is hidden or you are not sure about an item, put it in "unsure" instead of guessing 0.
Return JSON only: {{"inside_fridge_visible": true|false, "items": {{"milk": 1, "eggs": 0}}, "unsure": ["cheese"]}}"""


def signature(jpeg: bytes) -> list[int]:
    return list(Image.open(io.BytesIO(jpeg)).convert("L").resize((16, 12)).tobytes())


def changed(a: list[int], b: list[int]) -> bool:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a)) > CHANGE


def scan_camera(tid: str, cam: str, r, force: bool = False, now: float | None = None) -> str:
    """One look at one camera. Returns a short status (also what the tests assert on)."""
    now = now or time.time()
    items = [i for i in inventory.list_items(tid) if i["camera_id"] == cam]
    if not items:
        return "no tracked items on this camera"
    jpeg = bus.get_latest(tid, cam)
    if jpeg is None:
        return "no frame yet"
    key = f"invscan:{tid}:{cam}"
    prev = r.hgetall(key)
    sig = signature(jpeg)
    if prev and not force:
        age = now - float(prev["ts"])
        if age < SCAN_SEC or (age < REFRESH_SEC and not changed(sig, json.loads(prev["sig"]))):
            return "skipped: nothing changed"
    if settings.mock_mode or not llm.budget_ok(r, settings.spark_model):
        return "skipped: no model available (mock mode or daily limit reached)"
    names = [i["name"] for i in items]
    try:
        raw = llm.chat_json(settings.spark_model, [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(names=", ".join(names))}, llm.image_part(jpeg)]}],
            max_tokens=300, r=r, reasoning=False)
    except Exception:  # noqa: BLE001
        log.exception("fridge scan failed for %s/%s", tid, cam)
        return "model call failed"
    r.hset(key, mapping={"sig": json.dumps(sig), "ts": str(now)})
    r.expire(key, 7 * 86400)
    counts = inventory.parse_scan(raw, names)
    if counts is None:
        return "inside of the fridge not visible"
    need = inventory.apply_scan(tid, cam, counts, r)
    log.info("%s/%s fridge scan: %s | need to buy: %s", tid, cam, counts, need or "nothing")
    return f"updated {len(counts)} item(s), need: {', '.join(need) or 'nothing'}"


def tick() -> None:
    r = bus.redis_client()
    wanted: set[tuple[str, str, bool]] = set()
    while (raw := r.lpop("invscan:req")):                                 # "Scan now" clicks from the dashboard
        try:
            q = json.loads(raw)
            wanted.add((q["tenant"], q["camera"], True))
        except (ValueError, KeyError):
            continue
    for t in db.all_tenants():
        for cam in {i["camera_id"] for i in inventory.list_items(t["id"]) if i["camera_id"]}:
            wanted.add((t["id"], cam, False))
    for tid, cam, force in sorted(wanted, key=lambda w: not w[2]):
        try:
            res = scan_camera(tid, cam, r, force)
            if force:
                r.set(f"invscan:last:{tid}:{cam}", res, ex=600)
        except Exception:  # noqa: BLE001
            log.exception("scan of %s/%s failed", tid, cam)


if __name__ == "__main__":
    bus.run_periodic("fridge-inventory-agent", 10, tick)
