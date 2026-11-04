"""Grocery price agent (optional, compose profile `extras`).

Looks at the latest picture of one camera (default: the kitchen/fridge of the demo home) and asks Muse Spark for the
visible grocery items with a *rough* shelf price in INR. Results arrive as a dashboard notification, clearly marked as
an estimate read off the picture, never a real price list.

It goes through the shared LLM client, so it obeys the same daily call caps, provider chain and retries as every
other agent, and it never sends a made-up picture: with no frame (or no key / MOCK_MODE) it simply waits.
"""
from __future__ import annotations

import os
import time

from drishti import bus, llm
from drishti.config import settings
from drishti.log import get_logger

log = get_logger("grocery-price-agent")
TENANT = os.getenv("GROCERY_TENANT", "home_1_bangalore")
CAMERA = os.getenv("GROCERY_CAMERA", "fridge")
INTERVAL = max(60, int(os.getenv("GROCERY_INTERVAL_SEC", "900")))
CONDITIONS = {"fresh", "packaging_damaged", "near_expiry"}

PROMPT = (
    "You are a grocery vision assistant. List the grocery items clearly visible in this picture with a rough "
    "shelf price in INR and any visible discount badge. Do not guess items you cannot see. "
    'Return JSON only: {"items":[{"item_name":"","brand":"unknown","est_price_inr":0,'
    '"discount_detected":false,"condition":"fresh|packaging_damaged|near_expiry"}]}'
)


def clean_items(raw) -> list[dict]:
    """Keep only well-formed items; clamp absurd prices so a model slip cannot produce a wild total."""
    out = []
    for it in (raw.get("items") if isinstance(raw, dict) else raw) or []:
        if not isinstance(it, dict) or not isinstance(it.get("item_name"), str) or not it["item_name"].strip():
            continue
        try:
            price = min(100000.0, max(0.0, float(it.get("est_price_inr", 0))))
        except (TypeError, ValueError):
            price = 0.0
        cond = it.get("condition") if it.get("condition") in CONDITIONS else "fresh"
        out.append({"item_name": it["item_name"].strip()[:40], "brand": str(it.get("brand") or "unknown")[:30],
                    "est_price_inr": round(price), "discount_detected": bool(it.get("discount_detected")), "condition": cond})
    return out[:40]


def summarize(items: list[dict]) -> tuple[str, str]:
    total = sum(i["est_price_inr"] for i in items)
    deals = [i["item_name"] for i in items if i["discount_detected"]]
    worn = [i["item_name"] for i in items if i["condition"] != "fresh"]
    detail = "Estimated from the picture, not a price list. " + (f"Discounts seen: {', '.join(deals[:5])}. " if deals else "") \
        + (f"Check condition: {', '.join(worn[:5])}." if worn else "")
    return f"Groceries in view: {len(items)} items, about Rs {total} (estimate)", detail.strip()


def once(r) -> int:
    if settings.mock_mode or not llm.budget_ok(r, settings.spark_model):
        return 0
    jpeg = bus.get_latest(TENANT, CAMERA)
    if not jpeg:
        log.info("no recent picture for %s/%s - nothing to price", TENANT, CAMERA)
        return 0
    res = llm.chat_json(settings.spark_model, [{"role": "user", "content": [{"type": "text", "text": PROMPT}, llm.image_part(jpeg)]}],
                        max_tokens=600, r=r, reasoning=False)
    items = clean_items(res)
    if items:
        title, detail = summarize(items)
        bus.notify(TENANT, "info", "INFO", title, detail, r=r)
    return len(items)


def main() -> None:
    r = bus.redis_client()
    log.info("grocery price agent on %s/%s every %ss", TENANT, CAMERA, INTERVAL)
    while not bus.stopping():
        try:
            bus.heartbeat("grocery-price-agent", r)
            once(r)
        except Exception:  # noqa: BLE001
            log.exception("grocery pass failed")
        for _ in range(INTERVAL // 10):
            if bus.stopping():
                return
            bus.heartbeat("grocery-price-agent", r)
            time.sleep(10)


if __name__ == "__main__":
    main()
