"""Action agent: proofed -> persistence + actuation.

* every frame -> Postgres event row (dashboard timeline); non-trivial frames -> Neo4j Event
* ALERT/CRITICAL -> de-duplicated by (tenant, rule) cooldown, then: alert row (+ image proof), actuations
  chosen by the vertical's policy:
    edge kinds   speak / oled / isd1820      -> Redis list actuate:{tenant}, polled by the Pi (GET /edge/{t}/actuate)
    remote kinds notify_* / whatsapp / call  -> NOTIFY_WEBHOOK_URL (Slack-compatible JSON); "logged" if unset
"""
from __future__ import annotations

import json
import threading
import time

import httpx

from drishti import bus, db, graph
from drishti.config import settings
from drishti.log import get_logger
from drishti.verticals import RANK, VERTICALS

log = get_logger("action-agent")
EDGE_KINDS = {"speak", "oled", "isd1820"}
OLED_TEXT = {"fall_detected": "FALL!", "patient_on_floor": "FALL!", "monitor_alarm": "ALARM", "help_called": "HELP!"}


def oled_text(rule: str, level: str) -> str:
    return OLED_TEXT.get(rule) or (rule.split("_")[0].upper()[:9] + "!" if rule else level)


def build_payload(kind: str, p: dict, alert_id: str) -> dict:
    if kind == "speak":
        return {"text": p.get("speak_text") or p.get("reason", "Alert"), "volume": 1.0 if p["level"] == "CRITICAL" else 0.7}
    if kind == "oled":
        return {"text": oled_text(p.get("rule", ""), p["level"]), "blink": p["level"] == "CRITICAL"}
    if kind == "isd1820":
        return {"duration_sec": 6}
    return {"message": f"[{p['level']}] {p['tenant_id']}: {p.get('reason', '')}", "alert_id": alert_id,
            "image_url": f"/alerts/{alert_id}/image" if p.get("has_proof") else None}


def deliver_remote(kind: str, payload: dict, p: dict) -> str:
    if not settings.notify_webhook_url:
        log.info("[notify:%s] %s", kind, payload["message"])
        return "logged"
    try:
        resp = httpx.post(settings.notify_webhook_url, timeout=10, json={
            "text": f"{payload['message']} ({kind})", "kind": kind, "tenant_id": p["tenant_id"],
            "level": p["level"], "rule": p.get("rule"), "alert_id": payload["alert_id"]})
        resp.raise_for_status()
        return "sent"
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook failed for %s: %s", kind, exc)
        return "failed"


def cooldown_allows(r, tid: str, rule: str, level: str) -> bool:
    """One alert per (tenant, rule) per cooldown; a higher level always gets through."""
    key = f"cooldown:{tid}:{rule or 'llm'}"
    prev = r.get(key)
    if prev and RANK.get(prev, 0) >= RANK[level]:
        return False
    r.set(key, level, ex=settings.alert_cooldown_sec)
    return True


def handle(p: dict, r) -> None:
    tid, level, vertical = p["tenant_id"], p["level"], p["vertical"]
    labels = p.get("involved") or sorted({o["label"] for o in p.get("objects") or []})
    # idempotent on frame_id, so a retried message cannot duplicate the event
    db.save_event(tid, p["frame_id"], p.get("camera_id", "cam_1"), level, p.get("decision"), p.get("reason", ""),
                  p.get("rationale", ""), labels, p.get("ts"))

    alert = None
    if level != "NORMAL" and cooldown_allows(r, tid, p.get("rule", ""), level):
        image = bus.get_proof(p["frame_id"]) if p.get("has_proof") else None
        kinds = VERTICALS[vertical]["policy"][level]
        alert_id = db.save_alert(tid, vertical, level, p.get("rule", ""), p.get("reason", ""), p.get("rationale", ""),
                                 p.get("speak_text", ""), labels, kinds, p["frame_id"], image)
        alert = {"id": alert_id, "reason": p.get("reason", ""), "speak_text": p.get("speak_text", "")}
        for kind in kinds:
            payload = build_payload(kind, p, alert_id)
            if kind in EDGE_KINDS:
                act_id = db.save_actuation(tid, alert_id, kind, payload, "queued")
                key = f"actuate:{tid}"
                r.rpush(key, json.dumps({"id": act_id, "kind": kind, "payload": payload, "alert_id": alert_id}))
                r.ltrim(key, -100, -1)
            else:
                status = deliver_remote(kind, payload, p)
                db.save_actuation(tid, alert_id, kind, payload, status)
        try:
            bus.notify(tid, "alert", "CRITICAL" if level == "CRITICAL" else "WARNING", f"{level}: {p.get('reason', '')}",
                       p.get("rationale", ""), key=alert_id, r=r)
        except Exception:  # noqa: BLE001 - the bell is a convenience, never the alert path
            log.exception("dashboard notification failed")
        log.warning("ALERT %s %s/%s: %s", level, tid, p.get("rule"), p.get("reason"))
    elif level != "NORMAL":
        log.info("alert for %s/%s suppressed by cooldown", tid, p.get("rule"))

    if not p.get("static") or level != "NORMAL":
        try:
            graph.record_event(tid, p["frame_id"], p.get("camera_id", "cam_1"), p.get("ts") or bus.utcnow_iso(), level,
                               p.get("rule", ""), labels, p.get("ocr_texts") or [], len(p.get("objects") or []), alert)
        except Exception:  # noqa: BLE001 - the graph is memory, not the alert path
            log.exception("graph write failed (event kept in Postgres)")


def retention_loop() -> None:
    while not bus.stopping():
        try:
            db.prune(settings.graph_event_ttl_days)
            graph.prune(settings.graph_event_ttl_days)
        except Exception:  # noqa: BLE001
            log.exception("retention sweep failed")
        for _ in range(360):  # hourly, but wake quickly on shutdown
            if bus.stopping():
                return
            time.sleep(10)


if __name__ == "__main__":
    threading.Thread(target=retention_loop, daemon=True).start()
    bus.consume("proofed", "action-agent", handle)
