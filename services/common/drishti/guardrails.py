"""Guardrails: small, deterministic checks that sit around every model call.

The model proposes, these functions decide what is allowed to leave the building. They never call a model, never
need the network, and run in MOCK_MODE too, so they are fully unit-tested.

  G1  validate_llm      structure: level / rule / list sizes / text lengths, nothing unexpected gets through
  G2  clean_speech      what may be SPOKEN or MESSAGED: no accusations, no medical orders, no identity claims
  G3  evidence_gate     a model-only CRITICAL on a picture it could not see well is held at ALERT
  G4  habit limits      live in learning.py (protected rules, expiry, minimum evidence)
  G6  scrub_untrusted   OCR text, audio transcripts and owner notes are DATA, never instructions
  G5  StormBreaker      too many alerts in a short time -> one digest instead of a flood
"""
from __future__ import annotations

import re
import time
from typing import Any

from .verticals import LEVELS, RANK

# ------------------------------------------------------------------ G6: untrusted text -> data
_INJECTION = re.compile(
    r"(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|the)\b[^.\n]{0,40}"
    r"\b(instruction|rule|prompt|polic|guard|sop)\w*"
    r"|\b(system|assistant|developer)\s*(prompt|message|:)"
    r"|\byou\s+are\s+(now|no longer)\b"
    r"|\b(report|answer|respond|reply|return|output|say)\b[^.\n]{0,25}\b(normal|no\s+hazard|all\s+clear|safe)\b"
    r"|\"?level\"?\s*:\s*\"?(normal|alert|critical)\b"
    r"|\bdo\s+not\s+(alert|raise|report|notify)\b",
    re.I,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_FENCE = re.compile(r"[`{}<>]")


def scrub_untrusted(text: Any, limit: int = 200) -> str:
    """Text that came from the world (a sign, a voice, a form field): strip anything that reads like an instruction
    to the model, control characters and JSON/markup characters, then cap its length."""
    s = _CONTROL.sub(" ", str(text or ""))
    s = _INJECTION.sub("[removed]", s)
    s = _FENCE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()[:limit]


def scrub_list(items: Any, limit: int = 200, count: int = 20) -> list[str]:
    return [scrub_untrusted(t, limit) for t in (items if isinstance(items, list) else [])[:count] if t is not None]


# ------------------------------------------------------------------ G2: what may be said aloud / sent
_ACCUSE = re.compile(r"\byou\s+(are|'re)\s+(a\s+)?(thief|criminal|guilty|liar|terrorist|abuser|drunk|junkie)\b|\b(thief|criminal|terrorist)\s+(is|has)\b", re.I)
_MEDICAL = re.compile(r"\b(administer|inject|give\s+(him|her|them|the patient)\s+\d|take\s+\d+\s*(mg|ml|mcg|tablet|pill)|increase\s+the\s+dose|stop\s+the\s+medication|diagnos(is|ed)|prescri(be|ption))\b", re.I)
_IDENTITY = re.compile(r"\b(this\s+is|identified\s+as|recognis?ed\s+as|looks\s+like)\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b")
_SAFE_SPEECH = {
    "CRITICAL": "Emergency detected. Help is being called.",
    "ALERT": "Please check this area.",
    "NORMAL": "",
}


def clean_speech(text: Any, level: str = "ALERT") -> tuple[str, bool]:
    """Returns (text, was_replaced). Anything that accuses a person, gives a medical order or names someone from
    their looks is replaced by a neutral sentence for that level. Drishti describes events; it does not judge people."""
    s = _CONTROL.sub(" ", str(text or "")).strip()[:200]
    if _ACCUSE.search(s) or _MEDICAL.search(s) or _IDENTITY.search(s):
        return _SAFE_SPEECH.get(level, _SAFE_SPEECH["ALERT"]), True
    return s, False


# ------------------------------------------------------------------ G1: structure of a model reply
def validate_llm(out: dict, allowed_rules: list[str], custom_ids: set[str] | None = None) -> tuple[dict, list[str]]:
    """Normalise a Spark reply. Returns (clean_reply, flags). Flags are short strings kept for the audit trail."""
    flags: list[str] = []
    if not isinstance(out, dict):
        return {"level": "NORMAL", "rule": "llm_assessment", "reason": "", "rationale": "", "objects": [], "speak_text": ""}, ["not_an_object"]
    level = str(out.get("level", "NORMAL")).upper()
    if level not in LEVELS:
        flags.append(f"bad_level:{level[:12]}")
        level = "NORMAL"
    rule = out.get("rule") if isinstance(out.get("rule"), str) else "llm_assessment"
    objs = out.get("objects") if isinstance(out.get("objects"), list) else []
    if len(objs) > 12:
        flags.append("too_many_objects")
    speak, replaced = clean_speech(out.get("speak_text", ""), level)
    if replaced:
        flags.append("speech_replaced")
    reason = _CONTROL.sub(" ", str(out.get("reason", "")))[:300]
    rationale = _CONTROL.sub(" ", str(out.get("rationale", "")))[:600]
    if level != "NORMAL" and not reason.strip():
        flags.append("no_reason")
        reason = "Model flagged a possible hazard (no reason given)"
    return {"level": level, "rule": rule, "reason": reason, "rationale": rationale,
            "objects": [x for x in objs if isinstance(x, str)][:12], "speak_text": speak}, flags


# ------------------------------------------------------------------ G3: evidence gate
def picture_unreliable(quality: dict | None) -> str | None:
    q = quality or {}
    if q.get("obstructed"):
        return "camera view blocked"
    if q.get("enhanced"):
        return "dim picture, digitally brightened"
    if q.get("sharpness") is not None and q["sharpness"] < 15:
        return "blurry picture"
    return None


def evidence_gate(level: str, rule_hit: bool, quality: dict | None) -> tuple[str, str | None]:
    """A CRITICAL that only the model believes (no deterministic rule agrees) on a picture it could not see well is
    held at ALERT: a person still gets told, but the loudest alarm is kept for cases with real evidence."""
    why = picture_unreliable(quality)
    if level == "CRITICAL" and not rule_hit and why:
        return "ALERT", f"held at ALERT: model-only CRITICAL on a {why}"
    return level, None


# ------------------------------------------------------------------ G5: alert storm breaker
class StormBreaker:
    """`allow(r, tenant)` is True until `limit` alerts were raised inside `window_sec`; after that alerts are counted
    and one digest notification is due. CRITICAL always passes: a storm must never hide a real emergency."""

    def __init__(self, limit: int = 12, window_sec: int = 300):
        self.limit, self.window = limit, window_sec

    def check(self, r, tenant_id: str, level: str, now: float | None = None) -> str:
        """Returns 'ok', 'digest' (first suppressed one: send a summary) or 'suppressed'."""
        if level == "CRITICAL":
            return "ok"
        now = now or time.time()
        key = f"storm:{tenant_id}"
        pipe = r.pipeline()
        pipe.zadd(key, {f"{now}:{id(pipe)}:{time.monotonic_ns()}": now})
        pipe.zremrangebyscore(key, 0, now - self.window)
        pipe.zcard(key)
        pipe.expire(key, self.window * 2)
        n = pipe.execute()[2]
        if n <= self.limit:
            return "ok"
        return "digest" if r.set(f"stormflag:{tenant_id}", 1, nx=True, ex=self.window) else "suppressed"


def rank(level: str) -> int:
    return RANK.get(level, 0)
