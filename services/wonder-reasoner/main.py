"""Wonder agent (Muse Wonder + Neo4j): assessed -> decided.

Slow path, only for ALERT/CRITICAL. Pulls the tenant's habit graph, recent events and news context and decides:
  * a known habit explains an ALERT (e.g. Dad's 05:00 walk opens the door)  -> suppressed (NORMAL)
  * world context raises the stakes (bomb threat news + unattended bag)      -> ALERT escalated to CRITICAL
  * CRITICAL is never suppressed by habits or by the LLM
Without an LLM (MOCK_MODE / budget exhausted) the same policy runs deterministically on the graph data.
"""
from __future__ import annotations

from drishti import bus, graph, llm, rules
from drishti.config import settings
from drishti.log import get_logger
from drishti.verticals import LEVELS, RANK

log = get_logger("wonder-reasoner")

NEWS_ESCALATION = {
    "unattended_bag": ("bomb", "blast", "explosion", "terror", "threat", "suspicious package"),
    "crowd_density": ("stampede", "riot", "protest", "rally", "festival crowd"),
    "stranger_no_badge": ("kidnap", "abduct", "intruder"),
}

PROMPT = """You are Drishti Wonder, the slow, careful reasoner for tenant {tenant_id} ({vertical}).
Spark Guard raised {level} for rule '{rule}': {reason}
Local hour: {hour}:00
Habits learned for this tenant: {habits}
Recent events (24h): {events}
Local news context: {news}
Rules: never go below ALERT if Spark said CRITICAL. You may lower ALERT to NORMAL ONLY when a listed habit for the
same rule covers this hour. You may raise ALERT to CRITICAL when news context makes it more dangerous.
Return JSON only: {{"final_level":"NORMAL|ALERT|CRITICAL","habit_match":"<habit text or empty>",
"rationale":"<2-3 sentences explaining WHY>","speak_text":"<short sentence to say at the scene>"}}"""


def match_habit(habits: list[dict], rule: str, hour: int) -> dict | None:
    for h in habits:
        s, e = h.get("start_hour"), h.get("end_hour")
        if h.get("rule") == rule and s is not None and s <= hour < (e if e is not None else 24):
            return h
    return None


def news_hit(rule: str, news: list[dict]) -> str | None:
    words = NEWS_ESCALATION.get(rule, ())
    for n in news:
        text = (n.get("headline") or "").lower()
        if any(w in text for w in words):
            return n["headline"]
    return None


def handle(p: dict, r) -> None:
    tid, level, rule = p["tenant_id"], p["level"], p.get("rule", "")
    out = {"spark_level": level, "decision": "NORMAL", "habit_match": None, "news": []}

    if level == "NORMAL":
        bus.publish("decided", tid, {**p, **out}, r)
        return

    hour = rules.effective_hour(p, bus.local_hour())
    try:
        habits = graph.habits(tid)
        news = graph.news(tid, 24)
        events = graph.recent_events(tid, 24, 10)
    except Exception:  # noqa: BLE001 - graph down: decide without memory rather than drop the alert
        log.exception("graph unavailable - deciding without memory")
        habits, news, events = [], [], []

    habit = match_habit(habits, rule, hour) if rule else None
    headline = news_hit(rule, news)
    final, decision = level, "CONFIRMED"
    rationale, speak = p.get("rationale", ""), p.get("speak_text", "")

    if level == "ALERT" and habit:
        final, decision = "NORMAL", "SUPPRESSED_HABIT"
        rationale = f"Matches a known habit: {habit['desc']}. {rationale}".strip()
    elif level == "ALERT" and headline:
        final, decision = "CRITICAL", "ESCALATED_NEWS"
        rationale = f"Escalated because of local news: '{headline}'. {rationale}".strip()

    if not settings.mock_mode and llm.budget_ok(r):
        try:
            res = llm.chat_json(settings.wonder_model, [{"role": "user", "content": PROMPT.format(
                tenant_id=tid, vertical=p["vertical"], level=level, rule=rule, reason=p.get("reason", ""), hour=hour,
                habits=[h["desc"] for h in habits][:10] or "none", events=events or "none",
                news=[n["headline"] for n in news] or "none")}], max_tokens=400, r=r)
            wl = str(res.get("final_level", final)).upper()
            if wl in LEVELS:
                lowered = RANK[wl] < RANK[final]
                if not (lowered and (not habit or level == "CRITICAL")):  # downgrade only when the graph backs it
                    if wl != final:
                        decision = "LLM_ESCALATED" if RANK[wl] > RANK[final] else "LLM_SUPPRESSED_HABIT"
                    final = wl
            if res.get("rationale"):
                rationale = str(res["rationale"])[:600]
            if res.get("speak_text"):
                speak = str(res["speak_text"])[:200]
        except Exception:  # noqa: BLE001
            log.exception("Wonder call failed - keeping graph-policy decision")

    out.update({"decision": decision, "habit_match": habit["desc"] if habit else None,
                "news": [n["headline"] for n in news][:3]})
    log.info("%s %s/%s -> %s (%s)", tid, level, rule, final, decision)
    bus.publish("decided", tid, {**p, **out, "level": final, "rationale": rationale, "speak_text": speak}, r)


if __name__ == "__main__":
    bus.consume("assessed", "wonder-reasoner", handle)
