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
from datetime import datetime

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


def whatsapp_body(to: str, text: str) -> dict:
    """Cloud API message: an approved template (works any time) when WHATSAPP_TEMPLATE is set, else free text (24 h window only)."""
    text = text.replace("\n", " ")[:1000]
    if settings.whatsapp_template:
        return {"messaging_product": "whatsapp", "to": to, "type": "template",
                "template": {"name": settings.whatsapp_template, "language": {"code": settings.whatsapp_template_lang},
                             "components": [{"type": "body", "parameters": [{"type": "text", "text": text}]}]}}
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text, "preview_url": False}}


def deliver_whatsapp(payload: dict) -> str:
    url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_id}/messages"
    ok = 0
    for to in settings.whatsapp_to:
        try:
            resp = httpx.post(url, timeout=15, headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
                              json=whatsapp_body(to, payload["message"]))
            resp.raise_for_status()
            ok += 1
        except Exception as exc:  # noqa: BLE001
            body = getattr(getattr(exc, "response", None), "text", "")[:200]
            log.warning("WhatsApp to %s failed: %s %s", to[-4:], exc, body)
    return "sent" if ok == len(settings.whatsapp_to) else ("partial" if ok else "failed")


def whatsapp_enabled() -> bool:
    return bool(settings.whatsapp_token and settings.whatsapp_phone_id and settings.whatsapp_to)


def deliver_remote(kind: str, payload: dict, p: dict) -> str:
    if whatsapp_enabled():
        status = deliver_whatsapp(payload)
        if status == "sent" or not settings.notify_webhook_url:
            return status
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


def camera_watchdog_once(r, now: float | None = None) -> int:
    """A guard notices when a CCTV screen goes dead. Cameras that sent nothing for CAMERA_OFFLINE_SEC raise ONE notification
    until they report again (then one 'back' notice). Returns the number of notifications raised."""
    now = now or time.time()
    raised = 0
    for t in db.all_tenants():
        for cam in bus.list_cameras(t["id"]):
            if not cam.get("updated_at"):
                continue
            age = now - datetime.fromisoformat(cam["updated_at"]).timestamp()
            flag = f"camoffline:{t['id']}:{cam['camera_id']}"
            if age > settings.camera_offline_sec:
                if r.set(flag, 1, nx=True, ex=7 * 86400):
                    bus.notify(t["id"], "camera", "WARNING", f"Camera {cam['camera_id']} is silent for {int(age // 60)} min",
                               "No frame received: camera off, network down or the feed-ingestor cannot reach it.", r=r)
                    raised += 1
            elif r.delete(flag):
                bus.notify(t["id"], "camera", "INFO", f"Camera {cam['camera_id']} is back online", "Frames are arriving again.", r=r)
                raised += 1
    return raised


def watchdog_loop() -> None:
    r = bus.redis_client()
    while not bus.stopping():
        try:
            camera_watchdog_once(r)
        except Exception:  # noqa: BLE001
            log.exception("camera watchdog failed")
        for _ in range(6):
            if bus.stopping():
                return
            time.sleep(10)


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
    threading.Thread(target=watchdog_loop, daemon=True, name="camera-watchdog").start()
    bus.consume("proofed", "action-agent", handle)
