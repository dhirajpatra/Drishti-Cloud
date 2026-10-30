"""Spark agent (Muse Spark): perceived -> assessed.

Fuses IMAGES (frame + SAM masks) + VIDEO/sensors (PIR, ultrasonic, gesture) + TEXT (OCR, audio transcript)
+ DOCS (the vertical's SOP) into one decision. Deterministic rules always run; the LLM adds visual judgement.
Vertical-specific guidance lives in prompts/<vertical>.txt (edit + `docker compose restart spark-multimodal`).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from drishti import bus, db, llm, rules
from drishti.config import settings
from drishti.log import get_logger
from drishti.verticals import LEVELS, RANK, VERTICALS, max_level

log = get_logger("spark-multimodal")
PROMPT_DIR = Path(__file__).parent / "prompts"
_sop_cache: dict[str, tuple[float, str]] = {}

CORE_PROMPT = """You are Drishti Guard for vertical {vertical} for tenant {tenant_id}.
IMAGES: {masks}
VIDEO: motion={pir}, distance_front={us_front}cm, distance_door={us_door}cm, gesture={gesture}, temp={temp}C
TEXT: OCR={ocr}, Audio Transcript={audio}
DOCS: Safety SOP:
{sop}

{guidance}
Local time hour: {hour}:00. Rule engine already found: {findings}
Allowed rule ids: {rule_ids}
Decide: NORMAL, ALERT, CRITICAL. Explain the reason. List the objects involved.
Return JSON only: {{"level":"NORMAL|ALERT|CRITICAL","rule":"<one allowed rule id or llm_assessment>",
"reason":"<one short sentence>","rationale":"<2-3 sentences citing the SOP>","objects":["<labels>"],
"speak_text":"<short sentence to say aloud at the scene>"}}"""


def guidance(vertical: str) -> str:
    f = PROMPT_DIR / f"{vertical}.txt"
    return f.read_text().strip() if f.exists() else ""


def sop(vertical: str) -> str:
    hit = _sop_cache.get(vertical)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        text = db.get_sop(vertical) or VERTICALS[vertical]["sop"]
    except Exception:  # noqa: BLE001 - never block safety on a DB hiccup
        text = VERTICALS[vertical]["sop"]
    _sop_cache[vertical] = (time.time(), text)
    return text


def ask_spark(p: dict, jpeg: bytes, hits: list[dict], hour: int, r) -> dict | None:
    v, s = p["vertical"], p.get("sensors") or {}
    masks = [f"{o['label']}@{[round(x, 2) for x in o['bbox']]}" for o in (p.get("objects") or [])[:40]]
    prompt = CORE_PROMPT.format(
        vertical=v, tenant_id=p["tenant_id"], masks=", ".join(masks) or "none", pir=s.get("pir"),
        us_front=s.get("us_front"), us_door=s.get("us_door"), gesture=s.get("gesture"), temp=s.get("temp"),
        ocr=p.get("ocr_texts") or [], audio=str(s.get("audio_transcript") or "")[:200], sop=sop(v),
        guidance=guidance(v), hour=hour, findings=[h["rule"] for h in hits] or "none",
        rule_ids=VERTICALS[v]["rules"],
    )
    out = llm.chat_json(settings.spark_model, [{"role": "user", "content": [{"type": "text", "text": prompt}, llm.image_part(jpeg)]}],
                        max_tokens=350, r=r, reasoning=False)
    level = str(out.get("level", "NORMAL")).upper()
    if level not in LEVELS:
        level = "NORMAL"
    rule = out.get("rule")
    if isinstance(rule, str) and rules.norm_label(rule) == "violence":
        rule = "physical_abuse"                       # 'fight', 'abuse', 'assault' ... all mean the one abuse rule
    rule = rule if rule in VERTICALS[v]["rules"] else "llm_assessment"
    objs = [rules.norm_label(x) for x in out.get("objects", []) if isinstance(x, str)][:12]
    return {
        "level": level, "rule": rule, "reason": str(out.get("reason", ""))[:300],
        "rationale": str(out.get("rationale", ""))[:600], "objects": objs, "speak_text": str(out.get("speak_text", ""))[:200],
    }


def handle(p: dict, r) -> None:
    tid, cam, vertical = p["tenant_id"], p.get("camera_id", "cam_1"), p["vertical"]
    hour = rules.effective_hour(p, bus.local_hour())
    hits = rules.evaluate(vertical, p, r, bus.local_hour())
    last_key = f"lastassess:{tid}:{cam}"
    llm_res = None
    source = "rules"

    if p.get("static") and not hits and settings.low_cost_mode:
        cached = r.get(last_key)
        if cached:
            llm_res, source = json.loads(cached), "cache"
    elif not settings.mock_mode and llm.budget_ok(r, settings.spark_model):
        gated = settings.low_cost_mode and not hits and not r.set(f"sparkgate:{tid}:{cam}", 1, nx=True, ex=max(1, min(settings.sample_interval_sec, settings.min_analysis_gap_sec)))
        if not gated:
            jpeg = bus.get_frame(p["frame_id"])
            if jpeg is not None:
                try:
                    llm_res, source = ask_spark(p, jpeg, hits, hour, r), "spark"
                    r.set(last_key, json.dumps(llm_res), ex=3600)
                except Exception:  # noqa: BLE001 - API down: degrade to the rule engine, don't lose the frame
                    log.exception("Spark call failed - using rule engine only")
                    source = "rules_fallback"

    findings = [{"rule": h["rule"], "level": h["level"], "reason": h["reason"], "objects": h["objects"], "speak": h["speak"],
                 "source": "rule"} for h in hits]
    if llm_res and llm_res["level"] != "NORMAL":
        findings.append({"rule": llm_res["rule"], "level": llm_res["level"], "reason": llm_res["reason"],
                         "objects": llm_res["objects"], "speak": llm_res["speak_text"], "source": "llm"})

    if findings:
        top = max(findings, key=lambda f: RANK[f["level"]])  # ties keep the first (deterministic rule wins)
        level = max_level(*[f["level"] for f in findings])
        rationale = (llm_res or {}).get("rationale") if llm_res and llm_res["level"] != "NORMAL" else ""
        out = {"level": level, "rule": top["rule"], "reason": top["reason"], "involved": top["objects"],
               "speak_text": top["speak"],
               "rationale": rationale or f"Rule '{top['rule']}' matched against the {VERTICALS[vertical]['label']} SOP."}
    else:
        out = {"level": "NORMAL", "rule": "", "reason": (llm_res or {}).get("reason") or "No hazard detected",
               "involved": [], "speak_text": "", "rationale": (llm_res or {}).get("rationale", "")}

    log.info("%s/%s -> %s %s (%s)", tid, cam, out["level"], out["rule"] or "-", source)
    bus.publish("assessed", tid, {**p, **out, "findings": findings, "assess_source": source}, r)


if __name__ == "__main__":
    bus.consume("perceived", "spark-multimodal", handle)
