"""Learning from the operator's feedback (not a black-box RL loop).

Every alert can be marked CONFIRMED (a real event) or FALSE ALARM. For each (tenant, rule, hour) we keep a Beta
posterior with exponential time decay: P(false alarm) = (1 + F) / (2 + F + C), where F and C are the decayed counts of
false alarms and confirmations within one hour either side. It is a contextual bandit with a hard safety floor:

  * it can only ever turn an ALERT down to NORMAL (never a CRITICAL, never a protected rule)
  * it needs real evidence: at least MIN_EVIDENCE decayed false alarms, so one mis-click changes nothing
  * old feedback fades (half-life HALF_LIFE_DAYS), so a habit that stopped being true stops being believed
  * every decision is explainable: the dashboard shows the counts and the probability behind it
"""
from __future__ import annotations

from datetime import datetime, timezone

HALF_LIFE_DAYS = 14.0
WINDOW_DAYS = 60
MIN_EVIDENCE = 2.0        # decayed false alarms needed before anything is muted
SUPPRESS_P = 0.75         # posterior false-alarm probability needed to mute
BAND = 1                  # hours either side that count as "the same time of day"

# Never muted by learning: genuine emergencies and life-safety signals.
PROTECTED = frozenset({
    "physical_abuse", "fire_smoke", "weapon_visible", "fall_detected", "patient_on_floor", "help_called",
    "monitor_alarm", "seizure_or_convulsion", "patient_pulling_lines",
})


def _age_days(ts: datetime, now: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400)


def weight(ts: datetime, now: datetime) -> float:
    a = _age_days(ts, now)
    return 0.0 if a > WINDOW_DAYS else 0.5 ** (a / HALF_LIFE_DAYS)


def _near(h1: int, h2: int) -> bool:
    d = abs(h1 - h2) % 24
    return min(d, 24 - d) <= BAND


def stats(rows: list[dict], rule: str, hour: int, now: datetime | None = None) -> dict:
    """rows: feedback rows {rule, hour, verdict ('false_alarm'|'confirmed'), created_at}."""
    now = now or datetime.now(timezone.utc)
    f = c = 0.0
    for row in rows:
        if row["rule"] != rule or not _near(int(row["hour"]), hour):
            continue
        w = weight(row["created_at"], now)
        if row["verdict"] == "false_alarm":
            f += w
        elif row["verdict"] == "confirmed":
            c += w
    return {"false": round(f, 2), "confirmed": round(c, 2), "p_false": round((1 + f) / (2 + f + c), 3)}


def decide(rows: list[dict], rule: str, hour: int, now: datetime | None = None) -> dict:
    """Should an ALERT for `rule` at `hour` be muted because the operators keep saying it is a false alarm?"""
    if not rule or rule == "llm_assessment":
        return {"suppress": False, "why": "no stable rule to learn from"}
    if rule in PROTECTED:
        return {"suppress": False, "why": "protected rule: never muted"}
    s = stats(rows, rule, hour, now)
    if s["false"] < MIN_EVIDENCE:
        return {"suppress": False, **s, "why": f"needs more feedback ({s['false']}/{MIN_EVIDENCE} false alarms)"}
    if s["p_false"] < SUPPRESS_P:
        return {"suppress": False, **s, "why": f"confirmed events balance it out (p_false {s['p_false']})"}
    return {"suppress": True, **s,
            "why": f"{s['false']} false alarms vs {s['confirmed']} confirmed around {hour:02d}:00 (p_false {s['p_false']})"}


def summary(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """What the dashboard shows: one line per (rule, hour) that has any feedback, with the current decision."""
    now = now or datetime.now(timezone.utc)
    seen = sorted({(r["rule"], int(r["hour"])) for r in rows})
    out = []
    for rule, hour in seen:
        d = decide(rows, rule, hour, now)
        s = stats(rows, rule, hour, now)
        out.append({"rule": rule, "hour": hour, "false": s["false"], "confirmed": s["confirmed"], "p_false": s["p_false"],
                    "muted": d["suppress"], "why": d["why"]})
    out.sort(key=lambda x: (not x["muted"], -x["false"], x["rule"]))
    return out
