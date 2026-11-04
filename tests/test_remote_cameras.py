"""Remote cameras: URL hardening, staged diagnosis, site tokens, the edge agent and camera-only motion."""
import importlib.util
import io
import json
import socket
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

from drishti import camurl, rules, siteconfig

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("edge_agent", ROOT / "edge" / "camera-agent" / "agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["edge_agent"] = agent
_spec.loader.exec_module(agent)


def jpg(color=(120, 120, 120), box=None) -> bytes:
    im = Image.new("RGB", (160, 120), color)
    if box:
        im.paste((250, 250, 250), box)
    b = io.BytesIO()
    im.save(b, "JPEG")
    return b.getvalue()


# ------------------------------------------------------------------ URL helpers
def test_ngrok_tcp_url_becomes_rtsp():
    assert camurl.normalize_url("tcp://0.tcp.in.ngrok.io:12345/stream1") == "rtsp://0.tcp.in.ngrok.io:12345/stream1"
    assert siteconfig.parse_feeds("t:home_seniors:c=tcp://h:1/x")[0]["url"] == "rtsp://h:1/x"
    assert siteconfig.validate_source({"scheme": "tcp", "host": "0.tcp.in.ngrok.io", "port": 12345})["scheme"] == "rtsp"


def test_redact_hides_password():
    assert "secret" not in camurl.redact("rtsp://admin:secret@192.168.1.50/x")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "redis", "postgres", "gateway", "0.0.0.0", "metadata.google.internal"])
def test_forbidden_hosts(host):
    with pytest.raises(camurl.HostNotAllowed):
        camurl.check_host(host)
    with pytest.raises(siteconfig.ConfigError):
        siteconfig.validate_source({"scheme": "rtsp", "host": host})


def test_private_hosts_depend_on_deployment():
    camurl.check_host("192.168.1.50", allow_private=True)
    with pytest.raises(camurl.HostNotAllowed, match="edge agent"):
        camurl.check_host("192.168.1.50", allow_private=False)
    camurl.check_host("100.101.102.103", allow_private=False)          # Tailscale / CGNAT range is the VPN case
    camurl.check_host("0.tcp.in.ngrok.io", allow_private=False)         # a tunnel name
    camurl.check_host("drishti-kitchen")                                 # a LAN hostname still works


def test_guard_blocks_name_that_resolves_to_loopback(monkeypatch):
    monkeypatch.setattr(camurl, "resolve", lambda h: ["127.0.0.1"])
    with pytest.raises(camurl.HostNotAllowed):
        camurl.guard_url("rtsp://camera.example.com/x")


# ------------------------------------------------------------------ staged diagnosis
def _rtsp_server(reply: bytes):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        c.recv(512)
        c.sendall(reply)
        c.close()
        srv.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_diagnose_ok_and_login_required(monkeypatch):
    monkeypatch.setattr(camurl, "guard_url", lambda *a, **k: None)     # the test server lives on loopback
    port = _rtsp_server(b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\n\r\n")
    st = camurl.diagnose(f"rtsp://127.0.0.1:{port}/s")
    assert [s["stage"] for s in st] == ["dns", "tcp", "rtsp"] and all(s["ok"] for s in st)
    assert "login" in st[-1]["detail"]


def test_diagnose_names_the_failing_stage(monkeypatch):
    monkeypatch.setattr(camurl, "guard_url", lambda *a, **k: None)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                                          # nothing listens here now
    st = camurl.diagnose(f"rtsp://127.0.0.1:{port}/s")
    assert st[-1]["stage"] == "tcp" and not st[-1]["ok"] and "tunnel" in st[-1]["hint"]
    assert camurl.diagnose("rtsp://no-such-host.invalid/s")[-1]["stage"] == "dns"


def test_diagnose_refuses_forbidden_host():
    st = camurl.diagnose("rtsp://redis:6379/x")
    assert st[0]["stage"] == "host" and not st[0]["ok"]


# ------------------------------------------------------------------ site tokens
def _ingest(client, tenant, token=None, camera="edge_cam"):
    h = {"X-Edge-Token": token} if token else {}
    return client.post(f"/ingest/{tenant}", headers=h, files={"file": ("f.jpg", jpg(), "image/jpeg")},
                       data={"camera_id": camera, "sensors": json.dumps({"source": "edge-agent"}), "force": "true"})


def test_site_token_lifecycle_and_scope(client, redis_c):
    redis_c.flushdb()
    made = client.post("/tenants/mall_1/edge-tokens", json={"label": "ICU ward"})
    assert made.status_code == 201
    tok = made.json()["token"]
    assert tok.startswith("dre_") and len(tok) > 30
    listed = client.get("/tenants/mall_1/edge-tokens").json()
    assert listed and all("token" not in x and "token_hash" not in x for x in listed)

    assert _ingest(client, "mall_1", tok).status_code == 200
    assert _ingest(client, "school_1", tok).status_code == 401           # a token is for ONE tenant
    assert _ingest(client, "mall_1", "dre_wrong").status_code == 401     # a bad token fails even when AUTH_REQUIRED is off
    row = next(x for x in client.get("/tenants/mall_1/edge-tokens").json() if x["id"] == made.json()["id"])
    assert row["last_seen"] is not None

    assert client.delete(f"/tenants/mall_1/edge-tokens/{made.json()['id']}").json() == {"ok": True}
    assert _ingest(client, "mall_1", tok).status_code == 401             # revoked
    assert client.delete("/tenants/mall_1/edge-tokens/not-a-uuid").status_code == 404


def test_site_token_cannot_authorise_other_routes(client, monkeypatch):
    """With auth on, an edge token opens frame ingest only: it cannot list tokens, read alerts or mint more tokens."""
    import main as gw
    tok = client.post("/tenants/mall_1/edge-tokens", json={}).json()["token"]
    monkeypatch.setattr(gw.settings, "auth_required", True)
    h = {"X-Edge-Token": tok}
    assert client.get("/tenants/mall_1/alerts", headers=h).status_code == 401
    assert client.post("/tenants/mall_1/edge-tokens", json={}, headers=h).status_code == 401
    assert _ingest(client, "mall_1", tok).status_code == 200


# ------------------------------------------------------------------ edge agent
def test_agent_parses_cameras_and_redacts():
    cams = agent.parse_cameras("kitchen=rtsp://u:p@192.168.1.50/x, icu=tcp://h:1/y,bad name=rtsp://z, nourl=")
    assert cams == {"kitchen": "rtsp://u:p@192.168.1.50/x", "icu": "rtsp://h:1/y"}
    assert "p@" not in agent.redact("failed rtsp://u:p@192.168.1.50/x")


def test_agent_gate_sends_changes_and_keepalives_only():
    still, moved = jpg(), jpg(box=(20, 20, 120, 100))
    g = agent.Gate(interval=30, keepalive=120)
    assert g.decide(still, 1000) == (True, True)          # the first picture always goes
    assert g.decide(still, 1010) == (False, False)        # nothing changed: stay quiet
    assert g.decide(moved, 1015) == (False, True)         # changed, but too soon after the last send: held ...
    assert g.decide(moved, 1031) == (True, True)          # ... and sent as soon as the interval allows (never dropped)
    assert g.decide(moved, 1040) == (False, False)
    assert g.decide(moved, 1031 + 120) == (True, False)   # keep-alive: the dashboard keeps showing the camera online


def test_agent_needs_pairing_values():
    with pytest.raises(SystemExit):
        agent.load_config({"GATEWAY_URL": "http://x"})
    cfg = agent.load_config({"GATEWAY_URL": "http://x", "TENANT_ID": "t", "EDGE_TOKEN": "dre_1", "CAMERA_URL": "rtsp://h/x"})
    assert cfg["cams"] == {"cam_1": "rtsp://h/x"}


# ------------------------------------------------------------------ camera-only sites
def _p(static):
    return {"tenant_id": "home_1_bangalore", "camera_id": "kitchen", "objects": [], "static": static,
            "sensors": {"_sim": {"hour": 14, "elapsed": {"no_motion": 9000}}}}


def test_camera_only_site_motion_comes_from_picture_change(redis_c):
    redis_c.flushdb()
    quiet = rules.evaluate("home_seniors", _p(True), redis_c, 14)
    assert any(h["rule"] == "no_motion_2h" for h in quiet)             # a still picture for hours is still reported
    moving = rules.evaluate("home_seniors", _p(False), redis_c, 14)
    assert not any(h["rule"] == "no_motion_2h" for h in moving)        # a changing picture is motion: no false "no motion" alarm
