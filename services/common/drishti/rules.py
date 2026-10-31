"""Deterministic safety rules (run before and independently of any LLM).

They cover everything computable from sensors, SAM labels, OCR text and per-tenant state, so the
platform still protects people when the LLM budget is exhausted, the API is down, or in MOCK_MODE.
The LLM (Muse Spark) adds judgement for what pixels alone say (fall posture, fight, flame ...).

Each finding: {"rule", "level", "reason", "objects": [labels], "speak"}
"""
from __future__ import annotations

import re
import time
from typing import Any

import redis

from .config import settings

HELP_RE = re.compile(r"\b(help|bachao|save me|emergency|madad)\b", re.I)
ABUSE_RE = re.compile(r"\b(stop hitting|don'?t hit|dont hit|why are you hitting|let me go|leave me alone|mat maro|maar rahe|chhod do|chhodo)\b", re.I)
ALARM_RE = re.compile(r"\b(alarm|alert|critical|asystole|brady|tachy)\b", re.I)
HR_RE = re.compile(r"\bHR\D{0,3}(\d{2,3})\b", re.I)
SPO2_RE = re.compile(r"\bSP?O2\D{0,3}(\d{2,3})\b", re.I)

# labels the perception layer may emit, folded to one vocabulary
SYNONYMS = {
    "backpack": "bag", "suitcase": "bag", "luggage": "bag", "handbag": "bag",
    "people": "person", "man": "person", "woman": "person", "adult": "person", "human": "person",
    "kid": "child", "student": "child",
    "nurse": "staff", "doctor": "staff", "guard": "staff", "teacher": "staff",
    "person_on_floor": "fallen_person", "fallen": "fallen_person", "lying_person": "fallen_person",
    "burner": "stove", "stove_burner": "stove",
    "fight": "violence", "fighting": "violence", "hitting": "violence", "punching": "violence", "slapping": "violence",
    "kicking": "violence", "assault": "violence", "beating": "violence", "abuse": "violence", "physical_abuse": "violence",
    "physical_violence": "violence", "person_hitting": "violence", "choking": "violence",
    "flame": "fire", "flames": "fire", "burning": "fire", "blaze": "fire", "fire_smoke": "fire", "smoking": "smoke",
    "gun": "weapon", "pistol": "weapon", "firearm": "weapon", "rifle": "weapon", "handgun": "weapon", "weapon_visible": "weapon",
    "machete": "weapon", "sword": "weapon",
}

ABUSE_SPEAK = {
    "mall_security": "Stop immediately. Security has been called.",
    "home_seniors": "Stop that right now. Family has been informed.",
    "school_children": "Stop immediately. School staff are coming.",
    "icu_hospital": "Stop immediately. Security and the nurse station are alerted.",
}


def norm_label(label: str) -> str:
    key = re.sub(r"[\s\-]+", "_", str(label).strip().lower())
    return SYNONYMS.get(key, key)


def labels_of(objects: list[dict]) -> list[str]:
    return [norm_label(o.get("label", "")) for o in objects if o.get("label")]


def _hit(rule: str, level: str, reason: str, objects: list[str], speak: str) -> dict:
    return {"rule": rule, "level": level, "reason": reason, "objects": objects, "speak": speak}


def _elapsed(p: dict, key: str, default: float) -> float:
    """Elapsed seconds; `_sim.elapsed.<key>` lets the offline simulator fast-forward (MOCK_MODE only)."""
    if settings.mock_mode:
        sim = (p.get("sensors") or {}).get("_sim") or {}
        v = (sim.get("elapsed") or {}).get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return default


def effective_hour(p: dict, now_hour: int) -> int:
    if settings.mock_mode:
        h = ((p.get("sensors") or {}).get("_sim") or {}).get("hour")
        if isinstance(h, int) and 0 <= h <= 23:
            return h
    return now_hour


def _is_night(hour: int, cfg: dict | None = None) -> bool:
    th = (cfg or {}).get("thresholds") or {}
    s, e = th.get("night_start_hour", settings.night_start_hour), th.get("night_end_hour", settings.night_end_hour)
    return hour >= s or hour < e


def _in_hours(hours: list[int] | None, hour: int) -> bool:
    if not hours:
        return True
    a, b = hours
    return a <= hour < b if a < b else (hour >= a or hour < b)


def apply_overrides(hits: list[dict], cfg: dict, hour: int) -> list[dict]:
    """Admin settings on top of the built-in rules: switch off, change level, restrict to hours, per-alarm cooldown."""
    out = []
    for h in hits:
        rc = (cfg.get("rules") or {}).get(h["rule"])
        if rc:
            if rc.get("enabled") is False or not _in_hours(rc.get("hours"), hour):
                continue
            h = {**h, "level": rc.get("level") or h["level"]}
            if rc.get("cooldown_sec") is not None:
                h["cooldown_sec"] = rc["cooldown_sec"]
        out.append(h)
    return out


def label_rule_hits(cfg: dict, p: dict, r: redis.Redis, labels: list[str], hour: int, now: float) -> list[dict]:
    """Admin 'object rules': deterministic conditions on detected labels, optionally held for N seconds."""
    tid, cam = p["tenant_id"], p.get("camera_id", "cam_1")
    out = []
    for lr in cfg.get("label_rules") or []:
        if not lr.get("enabled", True):
            continue
        n = labels.count(lr["label"])
        cond = {"present": n >= 1, "absent": n == 0, "count_gte": n >= lr["value"]}[lr["op"]]
        held = _set_since(r, f"rstate:{tid}:{cam}:lr:{lr['id']}", cond, now)
        if held is None or not _in_hours(lr.get("hours"), hour):
            continue
        if _elapsed(p, f"lr_{lr['id']}", held) < lr.get("for_sec", 0):
            continue
        out.append({**_hit(f"obj_{lr['id']}", lr["level"], f"{lr['message']} ({cam})", [lr["label"]], lr["message"]),
                    "custom": True})
    return out


def _set_since(r: redis.Redis, key: str, present: bool, now: float) -> float | None:
    """Track how long a condition has held. Returns seconds held, or None when not present."""
    if not present:
        r.delete(key)
        return None
    r.set(key, now, nx=True, ex=86400)
    return now - float(r.get(key) or now)


def evaluate(vertical: str, p: dict, r: redis.Redis, now_hour: int, cfg: dict | None = None) -> list[dict]:
    """p is the `perceived` payload: tenant_id, camera_id, sensors, objects[], ocr_texts[]."""
    tid, cam = p["tenant_id"], p.get("camera_id", "cam_1")
    sensors: dict[str, Any] = p.get("sensors") or {}
    labels = labels_of(p.get("objects") or [])
    texts = [str(t) for t in (p.get("ocr_texts") or [])]
    audio = str(sensors.get("audio_transcript") or "")
    now = time.time()
    hour = effective_hour(p, now_hour)
    persons = labels.count("person") + labels.count("child")
    out: list[dict] = []
    cfg = cfg or {}
    th = cfg.get("thresholds") or {}

    def T(name: str):
        return th.get(name, getattr(settings, name))

    # ---- shared state: last motion (PIR) and last staff sighting
    # NX = start the clock the first time we see a tenant, so "never moves" still alerts after the threshold
    r.set(f"rstate:{tid}:last_motion", now, nx=True, ex=7 * 86400)
    r.set(f"rstate:{tid}:last_staff", now, nx=True, ex=7 * 86400)
    if sensors.get("pir") is True:
        r.set(f"rstate:{tid}:last_motion", now, ex=7 * 86400)
    if "staff" in labels:
        r.set(f"rstate:{tid}:last_staff", now, ex=7 * 86400)

    # ---- physical abuse / fight: every vertical, always CRITICAL and never suppressed by habits
    if "violence" in labels or ABUSE_RE.search(audio) or any(ABUSE_RE.search(t) for t in texts):
        out.append(_hit("physical_abuse", "CRITICAL", f"Possible physical abuse or fight in {cam}", ["violence"],
                        ABUSE_SPEAK.get(vertical, "Stop immediately. Help is being called.")))

    # ---- universal hazards, every vertical: fire / smoke, weapon, camera blocked
    if "fire" in labels or "smoke" in labels:
        out.append(_hit("fire_smoke", "CRITICAL", f"Fire or smoke visible in {cam}", [x for x in ("fire", "smoke") if x in labels],
                        "Fire or smoke detected. Please leave the area. Emergency services are being alerted."))
    if "weapon" in labels:
        out.append(_hit("weapon_visible", "CRITICAL", f"A weapon is visible in {cam}", ["weapon"],
                        "A weapon has been detected. Security has been alerted."))
    q = p.get("quality") or {}
    if q.get("obstructed"):
        out.append(_hit("camera_obstructed", "ALERT", f"Camera {cam} looks blocked, covered or blacked out ({q.get('why', 'view lost')})", [],
                        "The camera view is blocked. Please check the camera."))

    if vertical == "mall_security":
        crowd = persons
        if crowd > T("crowd_threshold"):
            out.append(_hit("crowd_density", "ALERT", f"Crowd of {crowd} people in {cam} exceeds {T("crowd_threshold")}",
                            ["person"], "Please keep moving and use all exits calmly."))
        bag_held = _set_since(r, f"rstate:{tid}:{cam}:bag_alone", "bag" in labels and persons == 0, now)
        held = _elapsed(p, "unattended_bag", bag_held or 0.0) if "bag" in labels else 0.0
        if "bag" in labels and held >= T("unattended_bag_sec"):
            out.append(_hit("unattended_bag", "ALERT", f"Bag left unattended for {int(held)}s in {cam}", ["bag"],
                            "Unattended bag detected. Security is on the way."))
        if "restricted_door" in labels and persons > 0 and "staff" not in labels:
            out.append(_hit("restricted_area", "ALERT", f"Non-staff person at a restricted door in {cam}",
                            ["restricted_door", "person"], "This area is restricted. Please step away."))

    elif vertical == "home_seniors":
        if "fallen_person" in labels:
            out.append(_hit("fall_detected", "CRITICAL", "A person appears to be on the floor (possible fall)",
                            ["fallen_person"], "Are you okay? Help is being called."))
        if HELP_RE.search(audio) or any(HELP_RE.search(t) for t in texts):
            out.append(_hit("help_called", "CRITICAL", f"Call for help heard: '{audio[:60]}'", ["person"],
                            "I heard you. Calling your family now."))
        last_motion = r.get(f"rstate:{tid}:last_motion")
        quiet = now - float(last_motion) if last_motion else 0.0
        quiet = _elapsed(p, "no_motion", quiet)
        if sensors.get("pir") is not True and quiet >= T("no_motion_sec") and not _is_night(hour, cfg):
            out.append(_hit("no_motion_2h", "ALERT", f"No motion for {int(quiet // 60)} minutes", [],
                            "I have not seen you move for a while. Are you alright?"))
        door = sensors.get("us_door")
        if isinstance(door, (int, float)) and door >= T("door_open_cm") and _is_night(hour, cfg):
            out.append(_hit("door_open_night", "ALERT", f"Front door open at night ({int(door)} cm clearance, {hour:02d}:00)",
                            ["door"], "The front door seems open. Is everything okay?"))
        if "stove" in labels and persons == 0:
            out.append(_hit("stove_on_unattended", "ALERT", "Stove in view with nobody in the kitchen", ["stove"],
                            "The stove may be on. Please check the kitchen."))

    elif vertical == "school_children":
        kids = labels.count("child")
        adults = persons - kids if persons >= kids else 0
        if kids >= 1 and adults == 0 and "staff" not in labels and "corridor" in labels and T("school_hours_start") <= hour < T("school_hours_end"):
            out.append(_hit("child_alone_corridor", "ALERT", "A child is alone in the corridor during class time",
                            ["child", "corridor"], "Please return to your classroom."))
        if adults >= 1 and "badge" not in labels and "staff" not in labels:
            near = kids > 0
            out.append(_hit("stranger_no_badge", "CRITICAL" if near else "ALERT",
                            "Adult without a visible school badge" + (" near children" if near else ""),
                            ["person"], "Visitors please report to the front office."))
        if "gate_open" in labels or ("gate" in labels and sensors.get("gate_open") is True):
            if T("school_hours_start") <= hour < T("school_hours_end") and "staff" not in labels:
                out.append(_hit("gate_open_class_time", "ALERT", "Main gate open during class hours with no staff at the gate",
                                ["gate"], "The school gate is open. Staff please attend."))
        if kids >= 4 and sensors.get("cluster") is True:
            out.append(_hit("bullying_cluster", "ALERT", "Group of children clustered around one child", ["child"],
                            "Everyone please step back and be kind."))

    elif vertical == "icu_hospital":
        if "fallen_person" in labels or "patient_on_floor" in labels:
            out.append(_hit("patient_on_floor", "CRITICAL", "Patient is on the floor", ["fallen_person"],
                            "Patient down. Nurse to the bedside immediately."))
        elif "patient_out_of_bed" in labels or ("patient" in labels and "bed" in labels and sensors.get("bed_occupied") is False):
            out.append(_hit("patient_out_of_bed", "ALERT", "Patient is out of bed", ["patient"],
                            "Patient is out of bed. Please assist."))
        joined = " ".join(texts)
        hr = [int(m) for m in HR_RE.findall(joined)]
        spo2 = [int(m) for m in SPO2_RE.findall(joined)]
        abnormal = any(h > 120 or h < 40 for h in hr) or any(s < 90 for s in spo2)
        if ALARM_RE.search(joined) or abnormal:
            out.append(_hit("monitor_alarm", "CRITICAL", f"Monitor shows alarm/abnormal vitals: '{joined[:80]}'", ["monitor"],
                            "Monitor alarm. Nurse to the bedside."))
        last_staff = r.get(f"rstate:{tid}:last_staff")
        absent = (now - float(last_staff)) if last_staff else 0.0
        absent = _elapsed(p, "staff_absent", absent)
        if absent >= T("staff_absent_sec") and "staff" not in labels:
            out.append(_hit("staff_absent_1h", "ALERT", f"No staff seen at the bedside for {int(absent // 60)} minutes", [],
                            "Bedside check is overdue."))
    out += label_rule_hits(cfg, p, r, labels, hour, now)
    return apply_overrides(out, cfg, hour) if cfg else out
