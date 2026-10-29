"""Gateway contract: validation, tenancy, auth, cost-control dedupe, Postgres row-level security."""
import io
import json

import pytest
from PIL import Image

from test_pipeline import jpeg


def test_health_and_verticals(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["mock_mode"] is True
    assert {v["id"] for v in client.get("/verticals").json()} == {
        "mall_security", "home_seniors", "school_children", "icu_hospital"}


def test_demo_tenants_seeded(client):
    ids = {t["id"] for t in client.get("/tenants").json()}
    assert {"mall_1", "home_1_bangalore", "school_1", "icu_ward_1"} <= ids
    assert "api_key" not in client.get("/tenants/mall_1").json()


def test_create_tenant_and_validation(client):
    r = client.post("/tenants", json={"id": "gym_1", "vertical": "mall_security", "name": "Gym"})
    assert r.status_code == 201 and r.json()["api_key"]
    assert client.post("/tenants", json={"id": "gym_1", "vertical": "mall_security"}).status_code == 409
    assert client.post("/tenants", json={"id": "Bad ID", "vertical": "mall_security"}).status_code == 422
    assert client.post("/tenants", json={"id": "x_1", "vertical": "nope"}).status_code == 422


def test_ingest_rejects_bad_input(client):
    assert client.post("/ingest/ghost", files={"file": ("f.jpg", jpeg(), "image/jpeg")}).status_code == 404
    assert client.post("/ingest/mall_1", files={"file": ("f.jpg", b"not an image", "image/jpeg")}).status_code == 415
    assert client.post("/ingest/mall_1", files={"file": ("f.jpg", jpeg(), "image/jpeg")},
                       data={"sensors": "[1]"}).status_code == 422
    assert client.post("/ingest/mall_1", files={"file": ("f.jpg", jpeg(), "image/jpeg")},
                       data={"camera_id": "../etc"}).status_code == 422


def test_large_frame_is_downscaled(client, redis_c):
    big = io.BytesIO()
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(big, "JPEG")
    r = client.post("/ingest/mall_1", files={"file": ("f.jpg", big.getvalue(), "image/jpeg")})
    from drishti import bus
    w, h = Image.open(io.BytesIO(bus.get_frame(r.json()["frame_id"]))).size
    assert w <= 640 and h <= 480


def test_static_scene_dedupe_saves_model_calls(client, redis_c):
    redis_c.flushdb()
    body = lambda **kw: client.post("/ingest/mall_1", files={"file": ("f.jpg", jpeg(3), "image/jpeg")},
                                    data={"sensors": json.dumps({"pir": False}), **kw})
    assert body().json()["static"] is False          # first sight of the scene
    assert body().json()["static"] is True           # same pixels, no PIR -> reuse cached analysis
    assert body(force="true").json()["static"] is False


def test_rate_limit(client, redis_c, monkeypatch):
    from drishti.config import settings
    redis_c.flushdb()
    monkeypatch.setattr(settings, "max_frames_per_min", 2)
    codes = [client.post("/ingest/school_1", files={"file": ("f.jpg", jpeg(), "image/jpeg")}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_auth_required(client, monkeypatch):
    from drishti.config import settings
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    assert client.get("/tenants").status_code == 401
    assert client.get("/tenants", headers={"X-Admin-Token": "s3cret"}).status_code == 200
    assert client.get("/tenants/mall_1/alerts").status_code == 401
    key = client.get("/tenants", headers={"X-Admin-Token": "s3cret"}).json()
    from drishti import db
    mall_key = db.get_tenant("mall_1")["api_key"]
    assert client.get("/tenants/mall_1/alerts", headers={"X-Tenant-Key": mall_key}).status_code == 200
    assert client.get("/tenants/school_1/alerts", headers={"X-Tenant-Key": mall_key}).status_code == 401  # no cross-tenant


def test_postgres_rls_isolates_tenants(client):
    """Even a query with no WHERE tenant_id only sees the scoped tenant's rows."""
    from drishti import db
    db.save_event("mall_1", "rls-frame-1", "cam_1", "NORMAL", None, "", "", [], None)
    db.save_event("school_1", "rls-frame-2", "cam_1", "NORMAL", None, "", "", [], None)
    with db._conn("mall_1") as c:
        rows = c.execute("SELECT frame_id FROM events WHERE frame_id LIKE 'rls-frame-%'").fetchall()
    assert [r["frame_id"] for r in rows] == ["rls-frame-1"]
    with db._conn("ghost") as c:
        assert c.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 0


def test_graph_and_stats_endpoints(client):
    g = client.get("/tenants/mall_1/graph").json()
    assert any("Tenant" in n["labels"] for n in g["nodes"])
    assert client.get("/verticals/mall_security/stats").status_code == 200
    assert client.get("/verticals/nope/stats").status_code == 404


def test_latest_frame_visible_even_when_deduped(client):
    """The live view shows the newest frame of every camera, including frames LOW_COST_MODE skips analysing."""
    for _ in range(2):  # second identical frame is 'static' and never reaches the models
        r = client.post("/ingest/mall_1", files={"file": ("f.jpg", jpeg(), "image/jpeg")}, data={"camera_id": "door_cam"})
        assert r.status_code == 200
    cams = {c["camera_id"]: c for c in client.get("/tenants/mall_1/cameras").json()}
    assert cams["door_cam"]["updated_at"]
    img = client.get("/tenants/mall_1/cameras/door_cam/latest")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    Image.open(io.BytesIO(img.content)).verify()
    assert client.get("/tenants/mall_1/cameras/nope/latest").status_code == 404
    assert client.get("/tenants/mall_1/cameras/bad..id/latest").status_code in (404, 422)
    assert client.get("/tenants/ghost/cameras").status_code == 404
