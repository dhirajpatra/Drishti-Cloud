"""Stock tracking: CRUD + tenant scope, counts from detections, low-stock notices, expiry sweep."""
from datetime import date, timedelta

from conftest import run_pipeline
from test_pipeline import post

from drishti import inventory


def _objs(*pairs):
    return {"pir": True, "_sim": {"hour": 14, "objects": [{"label": l, "count": n} for l, n in pairs]}}


def test_inventory_crud_and_tenant_scope(client):
    r = client.post("/tenants/home_1_bangalore/inventory", json={"name": "Curd Pot", "kind": "fridge", "min_stock": 1})
    assert r.status_code == 201 and r.json()["name"] == "curd_pot" and r.json()["status"] == "low"      # nothing seen yet
    names = {i["name"] for i in client.get("/tenants/home_1_bangalore/inventory").json()}
    assert "curd_pot" in names
    assert "curd_pot" not in {i["name"] for i in client.get("/tenants/mall_1/inventory").json()}        # tenant scoped
    assert client.post("/tenants/home_1_bangalore/inventory", json={"name": "x", "kind": "nope"}).status_code == 422
    assert client.post("/tenants/home_1_bangalore/inventory", json={"name": "bad/name"}).status_code == 422
    assert client.post("/tenants/home_1_bangalore/inventory", json={"name": "ok", "camera_id": "../x"}).status_code == 422
    assert client.get("/tenants/ghost/inventory").status_code == 404
    assert client.delete("/tenants/home_1_bangalore/inventory/curd_pot").status_code == 200
    assert client.delete("/tenants/home_1_bangalore/inventory/curd_pot").status_code == 404


def test_counts_follow_the_camera_and_low_stock_notifies(client, agents, redis_c):
    redis_c.flushdb()
    t = "school_1"
    client.post(f"/tenants/{t}/inventory", json={"name": "tester_juice", "kind": "fridge", "min_stock": 3})
    post(client, t, _objs(("tester_juice", 2)), camera_id="fridge")
    run_pipeline(redis_c, agents)
    item = next(i for i in client.get(f"/tenants/{t}/inventory").json() if i["name"] == "tester_juice")
    assert item["count"] == 2 and item["status"] == "low" and item["last_seen"]
    titles = [n["title"] for n in client.get(f"/tenants/{t}/notifications").json()["items"]]
    assert any("tester_juice is low" in x for x in titles)

    post(client, t, _objs(("tester_juice", 5)), camera_id="fridge")
    run_pipeline(redis_c, agents)
    item = next(i for i in client.get(f"/tenants/{t}/inventory").json() if i["name"] == "tester_juice")
    assert item["count"] == 5 and item["status"] == "ok"

    post(client, t, _objs(("person", 1)), camera_id="fridge")                  # shelf not in view: count must not drop to 0
    run_pipeline(redis_c, agents)
    item = next(i for i in client.get(f"/tenants/{t}/inventory").json() if i["name"] == "tester_juice")
    assert item["count"] == 5
    client.delete(f"/tenants/{t}/inventory/tester_juice")


def test_item_bound_to_a_camera_ignores_other_cameras(client, agents, redis_c):
    redis_c.flushdb()
    t = "icu_ward_1"
    client.post(f"/tenants/{t}/inventory", json={"name": "tester_saline", "kind": "medicine", "min_stock": 1,
                                                  "camera_id": "trolley", "count": 4})
    post(client, t, _objs(("tester_saline", 1)), camera_id="bed5")             # a different camera
    run_pipeline(redis_c, agents)
    item = next(i for i in client.get(f"/tenants/{t}/inventory").json() if i["name"] == "tester_saline")
    assert item["count"] == 4
    client.delete(f"/tenants/{t}/inventory/tester_saline")


def test_expiry_status_and_sweep(client, redis_c):
    redis_c.flushdb()
    t = "mall_1"
    past, soon = date.today() - timedelta(days=1), date.today() + timedelta(days=3)
    client.post(f"/tenants/{t}/inventory", json={"name": "tester_old", "min_stock": 0, "expiry": past.isoformat()})
    client.post(f"/tenants/{t}/inventory", json={"name": "tester_soon", "min_stock": 0, "expiry": soon.isoformat()})
    status = {i["name"]: i["status"] for i in client.get(f"/tenants/{t}/inventory").json()}
    assert status["tester_old"] == "expired" and status["tester_soon"] == "expiring"
    assert inventory.sweep(redis_c) >= 2
    titles = " | ".join(n["title"] for n in client.get(f"/tenants/{t}/notifications").json()["items"])
    assert "tester_old expired" in titles and "tester_soon expires in 3" in titles
    assert inventory.sweep(redis_c) == 0                                      # once, not every minute
    for n in ("tester_old", "tester_soon"):
        client.delete(f"/tenants/{t}/inventory/{n}")
