"""Admin page backend: dynamic configuration, camera connections (encrypted passwords), validation, and that agents obey it."""
import json

import pytest

from drishti import bus, db, rules, siteconfig, vault
from drishti.verticals import RULE_INFO, THRESHOLD_INFO, VERTICALS

T, H = "mall_1", "home_1_bangalore"


@pytest.fixture(autouse=True)
def clean(client):
    for t in (T, H):
        db.delete_site_config(t, "")
        db.delete_site_config(t, "cam_1")
        db.delete_camera_source(t, "cam_1")
    siteconfig.invalidate()
    yield
    siteconfig.invalidate()


def test_catalog_covers_every_rule_and_threshold():
    for k, v in VERTICALS.items():
        assert all(r in RULE_INFO for r in v["rules"])
        d = siteconfig.defaults_for(k)
        assert set(d["rules"]) == set(v["rules"])
        assert set(d["thresholds"]) == {t for t, i in THRESHOLD_INFO.items() if k in i[4]}


def test_only_real_customisations_are_stored_and_defaults_flow_through(client):
    cfg = client.get(f"/admin/tenants/{T}/config").json()
    assert cfg["overrides"] == {}
    body = {"thresholds": {"crowd_threshold": 40, "unattended_bag_sec": cfg["defaults"]["thresholds"]["unattended_bag_sec"]},
            "rules": {"restricted_area": {"enabled": False}}}
    new = client.put(f"/admin/tenants/{T}/config", json={"config": body}).json()
    assert new["overrides"] == {"thresholds": {"crowd_threshold": 40}, "rules": {"restricted_area": {"enabled": False}}}
    assert new["effective"]["thresholds"]["crowd_threshold"] == 40
    assert client.delete(f"/admin/tenants/{T}/config").json()["ok"]
    assert client.get(f"/admin/tenants/{T}/config").json()["overrides"] == {}


def test_camera_overrides_win_over_site(client):
    client.put(f"/admin/tenants/{T}/config", json={"config": {"sampling": {"sample_interval_sec": 120}}})
    client.put(f"/admin/tenants/{T}/config?camera=cam_1", json={"config": {"sampling": {"sample_interval_sec": 60}}})
    assert siteconfig.effective(T, "cam_1", "mall_security")["sampling"]["sample_interval_sec"] == 60
    assert siteconfig.effective(T, "cam_2", "mall_security")["sampling"]["sample_interval_sec"] == 120


@pytest.mark.parametrize("bad", [
    {"nope": {}}, {"rules": {"fall_detected": {"enabled": False}}},       # alarm of another vertical
    {"rules": {"crowd_density": {"level": "BOOM"}}}, {"thresholds": {"crowd_threshold": 1}},
    {"thresholds": {"no_motion_sec": 900}}, {"sampling": {"sample_interval_sec": 1}},
    {"notify": {"whatsapp_to": ["abc"]}}, {"notify": {"policy": {"ALERT": ["rm -rf"]}}},
    {"notify": {"webhook_url": "ftp://x"}}, {"custom_rules": [{"text": "hi"}]},
    {"label_rules": [{"label": "bag!", "message": "x"}]}, {"rules": {"crowd_density": {"hours": [5, 5]}}},
])
def test_validation_rejects_bad_input(client, bad):
    assert client.put(f"/admin/tenants/{T}/config", json={"config": bad}).status_code == 422


def test_rules_use_admin_thresholds_switches_and_levels(redis_c):
    p = {"tenant_id": T, "camera_id": "cam_1", "sensors": {}, "objects": [{"label": "person", "bbox": [0, 0, 1, 1]}] * 12}
    assert not [h for h in rules.evaluate("mall_security", p, redis_c, 12, siteconfig.defaults_for("mall_security")) if h["rule"] == "crowd_density"]
    cfg = siteconfig.merge(siteconfig.defaults_for("mall_security"), {"thresholds": {"crowd_threshold": 5}})
    hit = [h for h in rules.evaluate("mall_security", p, redis_c, 12, cfg) if h["rule"] == "crowd_density"]
    assert hit and hit[0]["level"] == "ALERT"                              # automatic level untouched by defaults
    cfg = siteconfig.merge(cfg, {"rules": {"crowd_density": {"level": "CRITICAL", "cooldown_sec": 60}}})
    hit = [h for h in rules.evaluate("mall_security", p, redis_c, 12, cfg) if h["rule"] == "crowd_density"]
    assert hit[0]["level"] == "CRITICAL" and hit[0]["cooldown_sec"] == 60
    cfg = siteconfig.merge(cfg, {"rules": {"crowd_density": {"hours": [22, 6]}}})
    assert not [h for h in rules.evaluate("mall_security", p, redis_c, 12, cfg) if h["rule"] == "crowd_density"]
    assert [h for h in rules.evaluate("mall_security", p, redis_c, 23, cfg) if h["rule"] == "crowd_density"]
    cfg = siteconfig.merge(cfg, {"rules": {"crowd_density": {"enabled": False}}})
    assert not [h for h in rules.evaluate("mall_security", p, redis_c, 23, cfg) if h["rule"] == "crowd_density"]


def test_stranger_near_children_keeps_automatic_critical(redis_c):
    p = {"tenant_id": "school_1", "camera_id": "g", "sensors": {}, "objects": [{"label": "person"}, {"label": "child"}]}
    hit = rules.evaluate("school_children", p, redis_c, 10, siteconfig.defaults_for("school_children"))
    assert [h["level"] for h in hit if h["rule"] == "stranger_no_badge"] == ["CRITICAL"]


def test_object_rules_fire_after_holding_for_the_set_time(redis_c):
    lr = {"id": "o1", "label": "person", "op": "absent", "value": 1, "for_sec": 120, "level": "ALERT", "message": "Counter unattended",
          "enabled": True, "hours": None}
    cfg = siteconfig.merge(siteconfig.defaults_for("mall_security"), {"label_rules": [lr]})
    base = {"tenant_id": T, "camera_id": "cam_1", "sensors": {}, "objects": []}
    redis_c.flushdb()
    assert not [h for h in rules.evaluate("mall_security", base, redis_c, 12, cfg) if h["rule"] == "obj_o1"]        # just started
    sim = {**base, "sensors": {"_sim": {"elapsed": {"lr_o1": 130}}}}
    hit = [h for h in rules.evaluate("mall_security", sim, redis_c, 12, cfg) if h["rule"] == "obj_o1"]
    assert hit and hit[0]["level"] == "ALERT" and "Counter unattended" in hit[0]["reason"]
    seen = {**base, "objects": [{"label": "person"}]}                                                              # condition ends -> clock resets
    assert not [h for h in rules.evaluate("mall_security", seen, redis_c, 12, cfg) if h["rule"] == "obj_o1"]


def test_camera_connection_password_is_encrypted_and_write_only(client):
    body = {"scheme": "rtsp", "host": "192.168.1.77", "port": 554, "path": "/stream1", "username": "admin", "password": "p@ss:/word"}
    out = client.put(f"/admin/tenants/{T}/cameras/cam_1", json=body).json()
    assert out["has_password"] is True and '"password"' not in json.dumps(out) and "p@ss" not in json.dumps(out)
    row = db.get_camera_source(T, "cam_1")
    assert "p@ss" not in row["password_enc"] and vault.decrypt(row["password_enc"]) == "p@ss:/word"
    feed = [f for f in siteconfig.all_feeds("") if f["camera"] == "cam_1" and f["tenant"] == T][0]
    assert feed["url"] == "rtsp://admin:p%40ss%3A%2Fword@192.168.1.77:554/stream1"
    # the IP changed: save again WITHOUT a password -> the stored one is kept
    client.put(f"/admin/tenants/{T}/cameras/cam_1", json={**body, "host": "192.168.1.88", "password": ""})
    feed = [f for f in siteconfig.all_feeds("") if f["camera"] == "cam_1" and f["tenant"] == T][0]
    assert "192.168.1.88" in feed["url"] and "p%40ss" in feed["url"]
    client.put(f"/admin/tenants/{T}/cameras/cam_1", json={**body, "password": "", "clear_password": True})
    assert db.get_camera_source(T, "cam_1")["password_enc"] == ""
    assert "p@ss" not in json.dumps(client.get(f"/admin/overview").json())


@pytest.mark.parametrize("bad", [
    {"scheme": "ftp", "host": "1.2.3.4"}, {"scheme": "http", "host": "169.254.169.254"}, {"scheme": "http", "host": "a b"},
    {"scheme": "http", "host": "http://1.2.3.4"}, {"scheme": "http", "host": "1.2.3.4", "port": 70000},
    {"scheme": "http", "host": "1.2.3.4", "path": "/a/../b"}, {"scheme": "file", "path": "/etc/passwd"},
])
def test_camera_validation(client, bad):
    assert client.put(f"/admin/tenants/{T}/cameras/cam_1", json=bad).status_code == 422


def test_env_camera_shows_in_overview_and_can_be_moved_to_database(client, monkeypatch):
    monkeypatch.setenv("RTSP_FEEDS", f"{H}:home_seniors:kitchen=http://viewer:secret@192.168.1.4:8081/")
    ov = client.get("/admin/overview").json()
    home = [t for t in ov["tenants"] if t["id"] == H][0]
    k = [c for c in home["cameras"] if c["camera_id"] == "kitchen"][0]
    assert k["source"] == "env"
    cam = client.get(f"/admin/tenants/{H}/cameras/kitchen").json()
    assert cam["host"] == "192.168.1.4" and cam["username"] == "viewer" and cam["has_password"] and "secret" not in json.dumps(cam)
    client.put(f"/admin/tenants/{H}/cameras/kitchen", json={**cam, "host": "192.168.1.9", "password": ""})
    url = [f for f in siteconfig.all_feeds(f"{H}:home_seniors:kitchen=http://viewer:secret@192.168.1.4:8081/") if f["camera"] == "kitchen"][0]["url"]
    assert url == "http://viewer:secret@192.168.1.9:8081/"                    # env password carried over on the first save
    db.delete_camera_source(H, "kitchen")


def test_connection_test_goes_through_the_ingestor(client, monkeypatch, redis_c):
    import importlib.util, pathlib, threading
    spec = importlib.util.spec_from_file_location("fi2", pathlib.Path(__file__).parents[1] / "services/feed-ingestor/main.py")
    fi = importlib.util.module_from_spec(spec); spec.loader.exec_module(fi)
    from test_pipeline import jpeg
    seen = {}
    monkeypatch.setattr(fi, "grab", lambda feed: seen.setdefault("url", feed["url"]) and jpeg())
    def worker():
        item = redis_c.blpop("camtest:req", timeout=10)
        fi.test_connection(item[1])
    th = threading.Thread(target=worker); th.start()
    res = client.post(f"/admin/tenants/{T}/cameras/cam_1/test", json={"scheme": "http", "host": "10.0.0.5", "username": "u", "password": "pw"}).json()
    th.join()
    assert res["ok"] is True and res["preview"].startswith("data:image/jpeg") and "u:pw" not in json.dumps(res)
    assert seen["url"] == "http://u:pw@10.0.0.5/"


def test_site_details_and_sop_and_feed_endpoints(client):
    out = client.put(f"/admin/tenants/{T}/site", json={"name": "Phoenix Mall - Gate B", "location": "Mysuru"}).json()
    assert out["location"] == "Mysuru"
    client.put(f"/admin/tenants/{T}/site", json={"name": "Phoenix Mall - Gate A", "location": "Bengaluru"})
    assert client.put(f"/admin/tenants/{T}/sop", json={"text": "short"}).status_code == 422
    r = client.post(f"/tenants/{T}/feeds", json={"camera_id": "cam_1", "url": "rtsp://u:p@10.0.0.2/s"})
    assert r.status_code == 201 and "p@" not in r.text
    assert client.get(f"/tenants/{T}/feeds").json()[0]["url"] == "rtsp://***@10.0.0.2/s"
    assert client.delete(f"/tenants/{T}/feeds/cam_1").json()["ok"]


def test_admin_endpoints_need_the_admin_token_when_auth_is_required(client, monkeypatch):
    from drishti.config import settings
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    assert client.get("/admin/overview").status_code == 401
    assert client.get(f"/admin/tenants/{T}/config").status_code == 401
    assert client.put(f"/admin/tenants/{T}/cameras/cam_1", json={"host": "1.2.3.4"}).status_code == 401
    assert client.get("/admin/overview", headers={"X-Admin-Token": "s3cret"}).status_code == 200


def test_action_agent_uses_site_notify_settings(monkeypatch):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("aa", pathlib.Path(__file__).parents[1] / "services/action-agent/main.py")
    aa = importlib.util.module_from_spec(spec); spec.loader.exec_module(aa)
    calls = []
    class R:
        def raise_for_status(self): pass
    monkeypatch.setattr(aa.httpx, "post", lambda url, **kw: calls.append(url) or R())
    out = aa.deliver_remote("notify_admin", {"message": "m", "alert_id": "a"}, {"tenant_id": T, "level": "ALERT"},
                            {"webhook_url": "https://hooks.example/site", "whatsapp_to": []})
    assert out == "sent" and calls == ["https://hooks.example/site"]
    assert aa.cooldown_allows(bus.redis_client(), "tx", "r1", "ALERT", cooldown=1)
