"""News agent vs freenewsapi.io as it really behaves: header auth, newest-first paging, in_title -> HTTP 500, q removed."""
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest

spec = importlib.util.spec_from_file_location("news_agent", pathlib.Path(__file__).parents[1] / "services/news-context/main.py")
nc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nc)

NOW = datetime.now(timezone.utc)


def row(title, hours_ago=1.0, uuid=None, pub="The Hindu"):
    return {"uuid": uuid or title, "title": title, "publisher": pub,
            "published_at": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}


class FakeAPI:
    """api.freenewsapi.io: x-api-key header, 10 rows per page via offset, in_title answers 500 (as on the live service)."""

    def __init__(self, feed, in_title_works=False, auth="x-api-key"):
        self.feed, self.in_title_works, self.auth, self.calls = feed, in_title_works, auth, []

    def __call__(self, url, headers=None, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, dict(headers or {}), params))
        req = httpx.Request("GET", url)
        h = {k.lower(): v for k, v in (headers or {}).items()}
        ok = h.get("x-api-key") == "KEY" if self.auth == "x-api-key" else h.get("authorization") == "Bearer KEY"
        if not ok:
            return httpx.Response(401, request=req, json={"error": "Missing API key"})
        if "q" in params:
            return httpx.Response(400, request=req, json={"error": "Search using the 'q' field has been removed."})
        if "in_title" in params:
            if not self.in_title_works:
                return httpx.Response(500, request=req, json={"error": "Internal Server Error"})
            rows = [r for r in self.feed if params["in_title"].lower() in r["title"].lower()][:10]
            return httpx.Response(200, request=req, json={"data": rows, "meta": {"has_more": False}})
        off = int(params.get("offset", 0))
        page = self.feed[off:off + 10]
        meta = {"has_more": off + 10 < len(self.feed), "next_offset": off + 10, "offset": off}
        return httpx.Response(200, request=req, json={"data": page, "meta": meta})


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(nc, "_auth_ok", None)
    monkeypatch.setattr(nc, "_host_ok", None)
    monkeypatch.setattr(nc, "API_URL", "")
    monkeypatch.setattr(nc, "_published_after_ok", True)
    monkeypatch.setattr(nc, "_in_title_retry_at", 0.0)
    monkeypatch.setattr(nc.settings, "news_api_key", "KEY")


def feed(n, local=(), every=7):
    """n newest-first rows; `local` titles are slipped in at every `every`-th position."""
    rows, loc = [], list(local)
    for i in range(n):
        rows.append(row(loc.pop(0), hours_ago=i / 4 + 0.1) if loc and i % every == 3 else row(f"Headline {i}", hours_ago=i / 4 + 0.1))
    return rows


# --------------------------------------------------------------------------------------------- local scan
def test_city_aliases():
    assert nc.city_names("Bengaluru") == ["Bengaluru", "Bangalore"]
    assert nc.city_names("Pune, Maharashtra") == ["Pune"]
    assert nc.city_names("") == []


def test_local_news_found_by_scanning_pages_newest_first(monkeypatch):
    api = FakeAPI(feed(50, local=["Bangalore metro line opens", "Bengaluru power cut tonight", "Bengaluru rains flood roads"]))
    monkeypatch.setattr(nc.httpx, "get", api)
    items = nc.fetch_local("Bengaluru")
    local = [n for n in items if "(national)" not in n["source"]]
    assert [n["headline"] for n in local] == ["Bangalore metro line opens", "Bengaluru power cut tonight", "Bengaluru rains flood roads"]
    assert len(items) == 5 and all("(national)" in n["source"] for n in items[3:])     # gap filled with national top
    assert [c[2].get("offset", 0) for c in api.calls if "in_title" not in c[2]] == [0, 10, 20, 30, 40]   # 5 pages scanned


def test_word_boundaries_and_no_false_match(monkeypatch):
    api = FakeAPI([row("Punekar wins cricket cup"), row("Pune bridge reopens"), row("Dispune over land")])
    monkeypatch.setattr(nc.httpx, "get", api)
    local = [n["headline"] for n in nc.fetch_local("Pune") if "(national)" not in n["source"]]
    assert local == ["Pune bridge reopens"]


def test_in_title_500_is_remembered_and_not_retried(monkeypatch):
    api = FakeAPI(feed(10))
    monkeypatch.setattr(nc.httpx, "get", api)
    nc.fetch_local("Delhi")
    n_in_title = sum(1 for c in api.calls if "in_title" in c[2])
    assert n_in_title >= 1 and nc._in_title_retry_at > NOW.timestamp()
    nc.fetch_local("Delhi")
    assert sum(1 for c in api.calls if "in_title" in c[2]) == n_in_title          # not retried for 6 h


def test_in_title_used_when_the_api_works_and_scan_is_short(monkeypatch):
    rows = feed(10) + [row("Chennai flood alert", hours_ago=30)]            # beyond page 1 of the scan
    api = FakeAPI(rows, in_title_works=True)
    monkeypatch.setattr(nc, "SCAN_PAGES", 1)
    monkeypatch.setattr(nc.httpx, "get", api)
    assert "Chennai flood alert" in [n["headline"] for n in nc.fetch_local("Chennai")]


def test_paging_stops_cleanly_when_offset_is_refused(monkeypatch):
    def api(url, headers=None, params=None, timeout=None):
        req = httpx.Request("GET", url)
        if "offset" in params:
            return httpx.Response(400, request=req, json={"error": "unknown parameter offset"})
        return httpx.Response(200, request=req, json={"data": [row("Delhi smog")], "meta": {"has_more": True, "next_offset": 10}})

    monkeypatch.setattr(nc.httpx, "get", api)
    assert [n["headline"] for n in nc.scan_country()] == ["Delhi smog"]


def test_q_parameter_is_never_sent(monkeypatch):
    api = FakeAPI(feed(20))
    monkeypatch.setattr(nc.httpx, "get", api)
    nc.fetch_local("Pune")
    assert not any("q" in c[2] for c in api.calls)


def test_stale_and_duplicate_rows_dropped():
    rows = [row("Fresh", 2), row("Fresh", 3, uuid="other"), row("Old", hours_ago=24 * 10), {"title": "", "published_at": None}]
    assert [n["headline"] for n in nc.normalise([rows[0], rows[0], rows[2], rows[3]])] == ["Fresh"]


# --------------------------------------------------------------------------------------------- endpoint / auth
def test_first_request_matches_documented_format(monkeypatch):
    """docs: curl "https://api.freenewsapi.io/v1/news?language=en&country=us" -H "x-api-key: YOUR_API_KEY" """
    api = FakeAPI(feed(3))
    monkeypatch.setattr(nc.httpx, "get", api)
    nc.scan_country(1)
    assert api.calls[0][0] == "https://api.freenewsapi.io/v1/news" and api.calls[0][1] == {"X-API-Key": "KEY"}
    assert len(api.calls) == 1


def test_auth_scheme_discovered_and_remembered(monkeypatch):
    api = FakeAPI(feed(3), auth="bearer")
    monkeypatch.setattr(nc.httpx, "get", api)
    nc.scan_country(1)
    assert nc._auth_ok == "bearer"
    before = len(api.calls)
    nc.scan_country(1)
    assert len(api.calls) - before == 1                                        # no re-probing once known


def test_wrong_key_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(nc.settings, "news_api_key", "WRONG")
    monkeypatch.setattr(nc.httpx, "get", FakeAPI(feed(3)))
    with pytest.raises(nc.NewsAuthError, match="NEWS_API_URL"):
        nc.scan_country(1)
        nc.fetch_local("Delhi")


def test_newsapi_org_rejected_key_gives_actionable_error(monkeypatch):
    def api(url, headers=None, params=None, timeout=None):
        req = httpx.Request("GET", url)
        return httpx.Response(401, request=req, json={"code": "apiKeyInvalid"})

    monkeypatch.setattr(nc.httpx, "get", api)
    with pytest.raises(nc.NewsAuthError, match="valid NewsAPI.org key"):
        nc.fetch_newsapi_org("Delhi")


def test_published_after_rejected_falls_back(monkeypatch):
    seen = []

    def api(url, headers=None, params=None, timeout=None):
        seen.append(dict(params))
        req = httpx.Request("GET", url)
        if "published_after" in params:
            return httpx.Response(422, request=req, json={"error": "bad filter"})
        return httpx.Response(200, request=req, json={"data": [row("Chennai flood alert")]})

    monkeypatch.setattr(nc.httpx, "get", api)
    assert nc.fetch_local("Chennai", scan=lambda: [])[0]["headline"] == "Chennai flood alert"
    assert nc._published_after_ok is False


def test_host_discovery_skips_dead_hosts_and_remembers(monkeypatch):
    calls = []

    def api(url, headers=None, params=None, timeout=None):
        calls.append(url)
        req = httpx.Request("GET", url)
        if "www." not in url:
            raise httpx.ConnectError("no such host", request=req)
        return httpx.Response(200, request=req, json={"data": [row("Pune rains")]})

    monkeypatch.setattr(nc.httpx, "get", api)
    assert nc.scan_country(1)[0]["headline"] == "Pune rains"
    assert nc._host_ok == "https://www.freenewsapi.io/v1/news"
    calls.clear()
    nc.scan_country(1)
    assert set(calls) == {"https://www.freenewsapi.io/v1/news"}


def test_redirect_across_hosts_keeps_the_key(monkeypatch):
    seen = []

    def api(url, headers=None, params=None, timeout=None):
        seen.append((url, (headers or {}).get("X-API-Key")))
        req = httpx.Request("GET", url)
        if url == "https://freenewsapi.io/v1/news":
            return httpx.Response(301, request=req, headers={"location": "https://www.freenewsapi.io/v1/news"})
        return httpx.Response(200, request=req, json={"data": [row("Delhi smog")]})

    monkeypatch.setattr(nc, "HOSTS", ["https://freenewsapi.io/v1/news"])
    monkeypatch.setattr(nc.httpx, "get", api)
    assert nc.scan_country(1)[0]["headline"] == "Delhi smog"
    assert seen[-1] == ("https://www.freenewsapi.io/v1/news", "KEY")


# --------------------------------------------------------------------------------------------- published_after
def test_server_error_with_published_after_retries_without_it(monkeypatch):
    seen = []

    def api(url, headers=None, params=None, timeout=None):
        seen.append(dict(params))
        req = httpx.Request("GET", url)
        if "published_after" in params:
            return httpx.Response(500, request=req, text="boom")
        return httpx.Response(200, request=req, json={"data": [row("Delhi smog")]})

    monkeypatch.setattr(nc.httpx, "get", api)
    assert nc.scan_country(1)[0]["headline"] == "Delhi smog"
    assert nc._published_after_ok is False and "published_after" not in seen[-1]


def test_outage_does_not_switch_the_filter_off(monkeypatch):
    monkeypatch.setattr(nc.httpx, "get", lambda url, headers=None, params=None, timeout=None:
                        httpx.Response(500, request=httpx.Request("GET", url), text="down"))
    with pytest.raises(httpx.HTTPStatusError):
        nc.scan_country(1)
    assert nc._published_after_ok is True


# --------------------------------------------------------------------------------------------- hazard notifications
def test_hazard_classification():
    assert nc.hazard("Bomb threat at Bengaluru mall")[0] == "CRITICAL"
    assert nc.hazard("Heavy rain: waterlogging in Bengaluru")[0] == "WARNING"
    assert nc.hazard("Bengaluru power cut tonight in HSR Layout") == ("WARNING", "power cut")
    assert nc.hazard("Bengaluru startup raises funding") is None
    assert nc.hazard("Firefly wins award") is None                      # word boundaries: 'fire' is not 'Firefly'


def test_only_local_hazards_notify_once(monkeypatch):
    sent = []
    monkeypatch.setattr(nc.bus, "notify", lambda tid, kind, level, title, detail, url, key, **kw: sent.append((tid, level, title, key)) or {"id": key})
    items = [{"headline": "Bengaluru flood warning", "source": "The Hindu", "url": "u", "ts": ""},
             {"headline": "Bomb threat in Delhi school", "source": "NDTV (national)", "url": None, "ts": ""},
             {"headline": "Bengaluru metro opens", "source": "The Hindu", "url": None, "ts": ""}]
    assert nc.notify_hazards("home_1", "Bengaluru", items) == 1
    assert sent[0][:2] == ("home_1", "WARNING") and "Bengaluru flood warning" in sent[0][2]
