"""News & context agent: timer-driven. Writes (:NewsContext) nodes linked to each tenant's location so the
Wonder agent can ground decisions (power cuts, protests, threats). With NEWS_API_KEY it queries newsapi.org;
in MOCK_MODE it writes one harmless demo item per tenant so the graph shows the shape.
"""
from __future__ import annotations

import httpx

from drishti import bus, db, graph
from drishti.config import settings
from drishti.log import get_logger

log = get_logger("news-context")
KEYWORDS = ('"power cut" OR outage OR "load shedding" OR protest OR riot OR stampede OR bomb OR threat OR '
            'kidnap OR flood OR "heat wave" OR curfew')


def fetch(location: str) -> list[dict]:
    resp = httpx.get(
        "https://newsapi.org/v2/everything", timeout=15,
        headers={"X-Api-Key": settings.news_api_key},
        params={"q": f'"{location}" AND ({KEYWORDS})', "language": "en", "sortBy": "publishedAt", "pageSize": 5},
    )
    resp.raise_for_status()
    return [{"headline": a["title"], "source": (a.get("source") or {}).get("name", "newsapi"), "url": a.get("url")}
            for a in resp.json().get("articles", []) if a.get("title")]


def tick() -> None:
    if not settings.news_api_key and not settings.mock_mode:
        log.info("no NEWS_API_KEY - news context disabled")
        return
    cache: dict[str, list[dict]] = {}
    for t in db.all_tenants():
        loc = (t.get("location") or "").strip()
        if not loc:
            continue
        try:
            if loc not in cache:
                cache[loc] = fetch(loc) if settings.news_api_key else [
                    {"headline": f"Planned power maintenance in {loc} this afternoon (demo item)", "source": "mock", "url": None}]
            for n in cache[loc]:
                graph.add_news(t["id"], n["headline"], n["source"], n.get("url"), bus.utcnow_iso())
        except Exception:  # noqa: BLE001
            log.exception("news refresh failed for %s", loc)


if __name__ == "__main__":
    bus.run_periodic("news-context", settings.news_interval_sec, tick)
