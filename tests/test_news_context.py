"""News agent: freenewsapi.io request shape, auth discovery, local-first top-5 selection."""
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest

spec = importlib.util.spec_from_file_location("news_agent", pathlib.Path(__file__).parents[1] / "services/news-context/main.py")
nc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nc)

NOW = datetime.now(timezone.utc)


def row(title, hours_ago=1, uuid=None, pub="The Hindu"):
    return {"uuid": uuid or title, "title": title, "publisher": pub,
            "published_at": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}


class FakeAPI:
    """Pretends to be freenewsapi.io: Bearer auth only, filters in_title, answers {'data': [...]}"""
    def __init__(self, local, national, auth="bearer"):
        self.local, self.national, self.auth, self.calls = local, national, auth, []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append((dict(headers or {}), dict(params or {})))
        ok = (headers or {}).get("Authorization") == "Bearer KEY" if self.auth == "bearer" else (headers or {}).get("X-API-Key") == "KEY"
        req = httpx.Request("GET", url)
        if not ok:
            return httpx.Response(401, request=req, json={"error": "unauthorized"})
        q = (params or {}).get("in_title")
        rows = [r for r in self.local if q and q.lower() in r["title"].lower()] if q else self.national
        return httpx.Response(200, request=req, json={"data": rows, "meta": {}})


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(nc, "_auth_ok", None)
    monkeypatch.setattr(nc, "_host_ok", None)
    monkeypatch.setattr(nc, "API_URL", "")
    monkeypatch.setattr(nc, "_published_after_ok", True)
    monkeypatch.setattr(nc.settings, "news_api_key", "KEY")


def test_city_aliases():
    assert nc.city_names("Bengaluru") == ["Bengaluru", "Bangalore"]
    assert nc.city_names("Pune, Maharashtra") == ["Pune"]
    assert nc.city_names("") == []


def test_local_headlines_first_newest_first_max_five(monkeypatch):
    local = [row(f"Bangalore story {i}", hours_ago=i + 1) for i in range(8)] + [row("Bengaluru power cut", hours_ago=0.5)]
    api = FakeAPI(local, [row("National thing")])
    monkeypatch.setattr(nc.httpx, "get", api)
    items = nc.fetch_local("Bengaluru")
    assert len(items) == 5 and items[0]["headline"] == "Bengaluru power cut"
    assert all("(national)" not in n["source"] for n in items)
    first_params = api.calls[0][1]
    assert first_params["language"] == "en" and first_params["country"] == "IN" and first_params["in_title"] == "Bengaluru"
    assert "published_after" in first_params


def test_gap_filled_with_national_top_and_labelled(monkeypatch):
    api = FakeAPI([row("Pune bridge reopens")], [row(f"National {i}", hours_ago=i + 1) for i in range(6)])
    monkeypatch.setattr(nc.httpx, "get", api)
    items = nc.fetch_local("Pune")
    assert len(items) == 5 and items[0]["headline"] == "Pune bridge reopens"
    assert [n["source"] for n in items[1:]] == ["The Hindu (national)"] * 4


def test_stale_and_duplicate_rows_dropped():
    rows = [row("Fresh", 2), row("Fresh", 3, uuid="other"), row("Old", hours_ago=24 * 10), {"title": "", "published_at": None}]
    got = nc.normalise([rows[0], rows[0], rows[2], rows[3]])
    assert [n["headline"] for n in got] == ["Fresh"]


def test_auth_scheme_discovered_and_remembered(monkeypatch):
    api = FakeAPI([row("Delhi smog")], [], auth="x-api-key")
    monkeypatch.setattr(nc.httpx, "get", api)
    nc.fetch_local("Delhi", national=lambda: [])
    assert nc._auth_ok == "x-api-key"
    n_before = len(api.calls)
    nc.fetch_local("Delhi", national=lambda: [])
    assert len(api.calls) - n_before == len(nc.city_names("Delhi"))      # no re-probing once known


def test_wrong_key_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(nc.settings, "news_api_key", "WRONG")
    monkeypatch.setattr(nc.httpx, "get", FakeAPI([], []))
    with pytest.raises(nc.NewsAuthError, match="NEWS_API_URL"):
        nc.fetch_local("Delhi")


def test_published_after_rejected_falls_back(monkeypatch):
    seen = []

    def api(url, headers=None, params=None, timeout=None):
        seen.append(dict(params))
        req = httpx.Request("GET", url)
        if "published_after" in params:
            return httpx.Response(422, request=req, json={"error": "bad filter"})
        return httpx.Response(200, request=req, json={"data": [row("Chennai flood alert")]})

    monkeypatch.setattr(nc.httpx, "get", api)
    assert nc.fetch_local("Chennai", national=lambda: [])[0]["headline"] == "Chennai flood alert"
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
    assert nc.fetch_local("Pune", national=lambda: [])[0]["headline"] == "Pune rains"
    assert nc._host_ok == "https://www.freenewsapi.io/v1/news"
    calls.clear()
    nc.fetch_local("Pune", national=lambda: [])
    assert set(calls) == {"https://www.freenewsapi.io/v1/news"}           # no re-probing


def test_redirect_across_hosts_keeps_authorization(monkeypatch):
    seen = []

    def api(url, headers=None, params=None, timeout=None):
        seen.append((url, (headers or {}).get("Authorization")))
        req = httpx.Request("GET", url)
        if url == "https://freenewsapi.io/v1/news":
            return httpx.Response(301, request=req, headers={"location": "https://www.freenewsapi.io/v1/news"})
        return httpx.Response(200, request=req, json={"data": [row("Delhi smog")]})

    monkeypatch.setattr(nc, "HOSTS", ["https://freenewsapi.io/v1/news"])
    monkeypatch.setattr(nc.httpx, "get", api)
    assert nc.fetch_local("Delhi", national=lambda: [])[0]["headline"] == "Delhi smog"
    assert seen[-1] == ("https://www.freenewsapi.io/v1/news", "Bearer KEY")


def test_first_request_matches_documented_format(monkeypatch):
    """docs: curl "https://api.freenewsapi.io/v1/news?language=en&country=us" -H "x-api-key: YOUR_API_KEY" """
    calls = []

    def api(url, headers=None, params=None, timeout=None):
        calls.append((url, dict(headers or {})))
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [row("Pune rains")]})

    monkeypatch.setattr(nc.httpx, "get", api)
    nc.fetch_local("Pune", national=lambda: [])
    assert calls[0][0] == "https://api.freenewsapi.io/v1/news" and calls[0][1] == {"X-API-Key": "KEY"}
    assert len(calls) == 1                                                    # worked first time: no probing
