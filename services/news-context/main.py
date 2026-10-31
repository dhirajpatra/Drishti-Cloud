"""News & context agent: timer-driven. Writes (:NewsContext) nodes linked to each tenant so the Wonder agent can
ground decisions (power cuts, protests, threats).

Local news = news about the place the tenant's cameras are in (tenant `location`, e.g. "Bengaluru"):
  1. the country's newest headlines are fetched page by page (NEWS_SCAN_PAGES, once per refresh for ALL tenants) and the
     ones whose title mentions the city or its other common names (Bengaluru/Bangalore ...) are picked locally -
     freenewsapi's own title search (`in_title`) returns HTTP 500 and `q` was removed, so the server cannot do this;
  2. if still short, `in_title` is tried once (skipped for 6 h after a failure);
  3. any remaining gap is filled with the country's top headlines (NEWS_COUNTRY).
Only the newest NEWS_MAX_ITEMS (default 5) per tenant are kept, and nothing older than NEWS_MAX_AGE_HOURS.

Providers (NEWS_PROVIDER):  freenewsapi (default, GET <NEWS_API_URL>?language=..&country=..&in_title=..)  |  newsapi (newsapi.org)
MOCK_MODE without a key writes one harmless demo item per tenant so the graph shows the shape.
"""
from __future__ import annotations

import functools
import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from drishti import bus, db, graph, siteconfig
from drishti.config import settings
from drishti.log import get_logger

log = get_logger("news-context")

PROVIDER = os.getenv("NEWS_PROVIDER", "freenewsapi").strip().lower()
API_URL = os.getenv("NEWS_API_URL", "").strip()          # empty = auto: try the hosts below and remember the first that answers
HOSTS = ["https://api.freenewsapi.io/v1/news", "https://freenewsapi.io/v1/news", "https://www.freenewsapi.io/v1/news"]  # 1st = per the docs
AUTH = os.getenv("NEWS_AUTH", "auto").strip()            # auto | bearer | x-api-key | query:<param name>
COUNTRY = os.getenv("NEWS_COUNTRY", "IN").strip()
LANGUAGE = os.getenv("NEWS_LANGUAGE", "en").strip()
ORDER_BY = os.getenv("NEWS_ORDER_BY", "").strip()        # provider specific; empty = provider default
MAX_ITEMS = max(1, int(os.getenv("NEWS_MAX_ITEMS", "5")))
MAX_AGE_H = max(1, int(os.getenv("NEWS_MAX_AGE_HOURS", "48")))
SCAN_PAGES = max(1, int(os.getenv("NEWS_SCAN_PAGES", "5")))   # pages (10 headlines each) scanned for city mentions

# Other names a city is reported under, so "Bengaluru" also finds "Bangalore" headlines.
ALIASES = {
    "bengaluru": ["Bengaluru", "Bangalore"], "bangalore": ["Bengaluru", "Bangalore"],
    "mumbai": ["Mumbai", "Bombay"], "kolkata": ["Kolkata", "Calcutta"], "chennai": ["Chennai", "Madras"],
    "gurugram": ["Gurugram", "Gurgaon"], "gurgaon": ["Gurugram", "Gurgaon"],
    "delhi": ["Delhi", "New Delhi"], "new delhi": ["New Delhi", "Delhi"],
}
AUTH_ORDER = ["x-api-key", "bearer", "query:api_key", "query:apikey", "query:key"]   # 1st = per the docs (-H "x-api-key: KEY")

_auth_ok: str | None = None            # auth scheme that worked, remembered for the process lifetime
_host_ok: str | None = None            # endpoint that worked
_in_title_retry_at = 0.0               # epoch seconds: skip the (currently broken) in_title search until then
_published_after_ok = True             # flips off if the API rejects the published_after filter


class NewsAuthError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- helpers
def city_names(location: str) -> list[str]:
    """'Bengaluru, Karnataka' -> ['Bengaluru', 'Bangalore']; unknown cities -> just the first part."""
    city = location.split(",")[0].strip()
    return ALIASES.get(city.lower(), [city])[:2] if city else []


def apply_auth(scheme: str, key: str, params: dict) -> tuple[dict, dict]:
    headers, p = {}, dict(params)
    if scheme == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif scheme == "x-api-key":
        headers["X-API-Key"] = key
    elif scheme.startswith("query:"):
        p[scheme.split(":", 1)[1]] = key
    else:
        raise ValueError(f"unknown NEWS_AUTH {scheme!r} (use auto, bearer, x-api-key or query:<name>)")
    return headers, p


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get(url: str, headers: dict, params: dict) -> httpx.Response:
    """GET following up to 3 redirects ourselves: httpx drops Authorization when a redirect changes host
    (freenewsapi.io -> www.freenewsapi.io), which would turn a valid key into a false 401."""
    for _ in range(4):
        resp = httpx.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code in (301, 302, 307, 308) and resp.headers.get("location"):
            url, params = str(httpx.URL(url).join(resp.headers["location"])).split("?")[0], params
            continue
        return resp
    return resp


def _request(params: dict) -> dict:
    """GET the freenewsapi endpoint, finding (and remembering) which host and auth scheme this account uses."""
    global _auth_ok, _host_ok
    hosts = [_host_ok] if _host_ok else ([API_URL] if API_URL else HOSTS)
    schemes = [_auth_ok] if _auth_ok else (AUTH_ORDER if AUTH == "auto" else [AUTH])
    last, unreachable = None, []
    for host in hosts:
        for scheme in schemes:
            headers, p = apply_auth(scheme, settings.news_api_key, params)
            try:
                resp = _get(host, headers, p)
            except httpx.TransportError as exc:               # DNS failure / refused: wrong host, try the next one
                unreachable.append(f"{host} ({type(exc).__name__})")
                break
            if resp.status_code == 404 and len(hosts) > 1:
                unreachable.append(f"{host} (404)")
                break
            if resp.status_code in (401, 403):
                last = resp
                continue
            if resp.status_code >= 400:
                log.error("news API %s -> HTTP %s: %s", host, resp.status_code, resp.text[:300].replace("\n", " "))
            resp.raise_for_status()
            if (_host_ok, _auth_ok) != (host, scheme):
                _host_ok, _auth_ok = host, scheme
                log.info("news API works with %s and auth scheme %r", host, scheme)
            return resp.json()
    detail = (f"HTTP {last.status_code} with every auth scheme tried ({', '.join(schemes)})" if last is not None
              else f"no usable endpoint ({'; '.join(unreachable) or 'none tried'})")
    raise NewsAuthError(f"{detail}. Check NEWS_API_KEY, and set NEWS_API_URL / NEWS_AUTH to what your freenewsapi.io "
                        "dashboard shows.")


def _search(extra: dict) -> tuple[list[dict], dict]:
    """One /v1/news call -> (rows, meta). `published_after` has no documented format, so if the API chokes on it
    (400/422/5xx) the same query is retried without it - and only if THAT works is the filter switched off
    (age is then checked locally)."""
    global _published_after_ok
    params = {"language": LANGUAGE, "country": COUNTRY, **extra}
    if ORDER_BY:
        params["order_by"] = ORDER_BY
    if _published_after_ok:
        after = (datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            data = _request({**params, "published_after": after})
            return data.get("data") or [], data.get("meta") or {}
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code not in (400, 422) and code < 500:
                raise
            log.warning("news API answered HTTP %s to a query with published_after - retrying without it", code)
            data = _request(params)                  # still failing -> raises, filter stays on
            _published_after_ok = False
            log.warning("published_after is not usable with this API - filtering by age locally from now on")
            return data.get("data") or [], data.get("meta") or {}
    data = _request(params)
    return data.get("data") or [], data.get("meta") or {}


def normalise(items: list[dict], now: datetime | None = None) -> list[dict]:
    """provider rows -> {headline, source, url, ts}; drops untitled / stale / duplicate rows; newest first."""
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(hours=MAX_AGE_H)
    seen, out = set(), []
    for a in items:
        title = (a.get("title") or "").strip()
        ts = parse_ts(a.get("published_at"))
        key = a.get("uuid") or title.lower()
        if not title or key in seen or (ts and ts < oldest) or (ts and ts > now + timedelta(hours=1)):
            continue
        seen.add(key)
        out.append({"headline": title[:300], "source": a.get("publisher") or a.get("source") or "news",
                    "url": a.get("url") or a.get("link"), "ts": (ts or now).isoformat()})
    return sorted(out, key=lambda n: n["ts"], reverse=True)


# ----------------------------------------------------------------------------------------------- providers
def scan_country(pages: int | None = None) -> list[dict]:
    """The country's newest headlines, `pages` x 10 of them, newest first (one API call per page)."""
    pages = pages or SCAN_PAGES
    rows: list[dict] = []
    offset = 0
    for i in range(pages):
        try:
            data, meta = _search({"offset": offset} if offset else {})
        except httpx.HTTPStatusError as exc:
            if i == 0:
                raise
            log.warning("paging stopped at page %d: HTTP %s (the API may not accept offset)", i + 1, exc.response.status_code)
            break
        rows += data
        nxt = meta.get("next_offset")
        if not data or not meta.get("has_more") or nxt is None or nxt == offset:
            break
        offset = nxt
    return normalise(rows)


def mentions(title: str, names: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(n)}\b", title, re.I) for n in names)


def fetch_local(location: str, scan=scan_country) -> list[dict]:
    """Top MAX_ITEMS headlines for a location (freenewsapi): city mentions first, then country top headlines.
    `scan` returns the country's newest headlines (memoised per refresh by the caller, shared by all tenants)."""
    global _in_title_retry_at
    names = city_names(location)
    pool = scan()
    items = [n for n in pool if mentions(n["headline"], names)][:MAX_ITEMS]

    if len(items) < MAX_ITEMS and names and time.time() >= _in_title_retry_at:
        try:                                                    # server-side title search, when the API lets us
            rows: list[dict] = []
            for name in names:
                rows += _search({"in_title": name})[0]
            have = {n["headline"].lower() for n in items}
            items += [n for n in normalise(rows) if n["headline"].lower() not in have]
            items = sorted(items, key=lambda n: n["ts"], reverse=True)[:MAX_ITEMS]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 422) and exc.response.status_code < 500:
                raise
            _in_title_retry_at = time.time() + 6 * 3600
            log.warning("news API in_title search failed (HTTP %s) - city matching is done locally; retry in 6 h",
                        exc.response.status_code)

    if len(items) < MAX_ITEMS:
        have = {n["headline"].lower() for n in items}
        for n in pool:
            if len(items) >= MAX_ITEMS:
                break
            if n["headline"].lower() not in have:
                items.append({**n, "source": f"{n['source']} (national)"})
    return items


def fetch_newsapi_org(location: str) -> list[dict]:
    """Legacy provider: newsapi.org, hazard keywords near the location."""
    kw = ('"power cut" OR outage OR "load shedding" OR protest OR riot OR stampede OR bomb OR threat OR '
          'kidnap OR flood OR "heat wave" OR curfew')
    resp = httpx.get("https://newsapi.org/v2/everything", timeout=15, headers={"X-Api-Key": settings.news_api_key},
                     params={"q": f'"{location}" AND ({kw})', "language": LANGUAGE, "sortBy": "publishedAt", "pageSize": MAX_ITEMS})
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise NewsAuthError(
                f"NewsAPI.org rejected NEWS_API_KEY (HTTP {exc.response.status_code}). "
                "Check that NEWS_API_KEY is a valid NewsAPI.org key."
            ) from exc
        raise
    now = bus.utcnow_iso()
    return [{"headline": a["title"], "source": (a.get("source") or {}).get("name", "newsapi"), "url": a.get("url"),
             "ts": a.get("publishedAt") or now} for a in resp.json().get("articles", []) if a.get("title")][:MAX_ITEMS]


HAZARD_CRITICAL = ("bomb", "blast", "explosion", "terror", "terrorist", "kidnap", "abduct", "stampede", "shooting", "gunfire",
                   "hostage", "riot", "curfew", "building collapse", "collapse")
HAZARD_WARNING = ("flood", "flooding", "heavy rain", "cyclone", "storm", "landslide", "earthquake", "fire", "power cut",
                  "power outage", "outage", "blackout", "shutdown", "bandh", "strike", "protest", "accident", "heatwave",
                  "advisory", "red alert", "orange alert", "waterlogging")
_HAZARD_RE = {lvl: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
              for lvl, words in (("CRITICAL", HAZARD_CRITICAL), ("WARNING", HAZARD_WARNING))}


def hazard(headline: str, extra: list[str] | None = None) -> tuple[str, str] | None:
    """(level, matched word) when a headline looks like something a security system should surface, else None."""
    for lvl in ("CRITICAL", "WARNING"):
        m = _HAZARD_RE[lvl].search(headline or "")
        if m:
            return lvl, m.group(1).lower()
    for w in extra or []:                                          # words the owner added in the Admin page
        if re.search(r"\b" + re.escape(w) + r"\b", headline or "", re.I):
            return "WARNING", w
    return None


def notify_hazards(tid: str, city: str, items: list[dict], extra: list[str] | None = None) -> int:
    """Dashboard notification for every LOCAL hazard headline (national filler is context only). De-duplicated per headline."""
    sent = 0
    for n in items:
        if "(national)" in n["source"]:
            continue
        hz = hazard(n["headline"], extra)
        if hz and bus.notify(tid, "news", hz[0], f"Local news ({city}): {n['headline']}", f"{n['source']} · matched '{hz[1]}'",
                             n.get("url"), key=hashlib.sha1(n["headline"].encode()).hexdigest()[:16]):
            sent += 1
    return sent


def tick() -> None:
    if not settings.news_api_key and not settings.mock_mode:
        log.info("no NEWS_API_KEY - news context disabled")
        return
    cache: dict[str, list[dict]] = {}
    scan = functools.lru_cache(maxsize=1)(scan_country)            # one set of country-level calls per refresh
    for t in db.all_tenants():
        loc = (t.get("location") or "").strip()
        if not loc:
            continue
        try:
            if loc not in cache:
                if not settings.news_api_key:
                    cache[loc] = [{"headline": f"Planned power maintenance in {loc} this afternoon (demo item)",
                                   "source": "mock", "url": None, "ts": bus.utcnow_iso()}]
                elif PROVIDER == "newsapi":
                    cache[loc] = fetch_newsapi_org(loc)
                else:
                    cache[loc] = fetch_local(loc, scan)
                log.info("news for %s (%s): %d item(s)", t["id"], loc, len(cache[loc]))
            for n in cache[loc]:
                graph.add_news(t["id"], n["headline"], n["source"], n.get("url"), n["ts"])
            nc = siteconfig.effective(t["id"], "", t["vertical"])["notify"]
            if nc["news_hazards"] and notify_hazards(t["id"], loc, cache[loc], nc["extra_hazard_words"]):
                log.info("hazard news notification(s) raised for %s", t["id"])
        except NewsAuthError as exc:
            log.error("news disabled until next refresh: %s", exc)
            return
        except Exception:  # noqa: BLE001
            log.exception("news refresh failed for %s", loc)


if __name__ == "__main__":
    bus.run_periodic("news-context", settings.news_interval_sec, tick)
