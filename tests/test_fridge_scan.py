"""Fridge camera -> everyday staples -> 'Need to buy' (vision model mocked)."""
import importlib.util
import io
import pathlib

import pytest
from PIL import Image

from drishti import bus, inventory, llm
from drishti.config import settings

spec = importlib.util.spec_from_file_location("fridge", pathlib.Path(__file__).parents[1] / "services/fridge-inventory-agent/main.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

T, CAM = "home_1_bangalore", "kitchen_t"


def pic(v=120):
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (v, v, v)).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(autouse=True)
def live(monkeypatch, client, redis_c):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(llm, "budget_ok", lambda r=None, model=None: True)
    redis_c.delete(f"invscan:{T}:{CAM}")
    for n, _, _ in inventory.STAPLES:
        inventory.delete_item(T, n)
    yield
    for i in inventory.list_items(T):
        if i["camera_id"] == CAM:
            inventory.delete_item(T, i["name"])


def test_staples_are_seeded_once_and_never_overwrite_owner_settings(client):
    r = client.post(f"/tenants/{T}/inventory/staples", json={"camera_id": CAM})
    assert r.status_code == 201 and {"eggs", "bread", "cheese", "butter", "milk", "vegetables"} <= set(r.json()["added"])
    client.post(f"/tenants/{T}/inventory", json={"name": "eggs", "kind": "fridge", "min_stock": 12, "camera_id": CAM})
    assert client.post(f"/tenants/{T}/inventory/staples", json={"camera_id": CAM}).json()["added"] == []
    assert next(i for i in client.get(f"/tenants/{T}/inventory").json() if i["name"] == "eggs")["min_stock"] == 12
    assert client.post(f"/tenants/{T}/inventory/staples", json={"camera_id": "../x"}).status_code == 422


def test_parse_only_trusts_what_the_model_judged():
    names = ["milk", "eggs", "bread", "vegetables"]
    assert inventory.parse_scan({"inside_fridge_visible": False, "items": {"milk": 0}}, names) is None     # door closed
    got = inventory.parse_scan({"inside_fridge_visible": True, "items": {"Milk": 2, "egg": 0, "veggies": 3, "bread": 1, "ghost": 5},
                                "unsure": ["bread"]}, names)
    assert got == {"milk": 2, "eggs": 0, "vegetables": 3}          # bread unsure -> untouched, unknown names dropped


def test_scan_updates_counts_and_raises_one_need_to_buy(monkeypatch, client, redis_c):
    client.post(f"/tenants/{T}/inventory/staples", json={"camera_id": CAM})
    redis_c.delete(f"notifseen:{T}")
    bus.put_latest(T, CAM, pic())
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"inside_fridge_visible": True, "unsure": ["cheese"],
                        "items": {"milk": 2, "eggs": 0, "bread": 0, "butter": 1, "vegetables": 3, "fruits": 0, "curd": 1, "juice": 1}})
    out = fa.scan_camera(T, CAM, redis_c)
    assert out.startswith("updated 8") and "eggs" in out and "bread" in out
    shop = client.get(f"/tenants/{T}/inventory/shopping").json()
    assert {i["name"] for i in shop["to_buy"] if i["camera_id"] == CAM} == {"eggs", "bread", "fruits"}
    assert "cheese" in {i["name"] for i in shop["unchecked"]}                  # unsure stays unchecked, not "out"
    notes = [n for n in client.get(f"/tenants/{T}/notifications").json()["items"] if n["kind"] == "inventory" and "Need to buy" in n["title"]]
    assert notes and "eggs" in notes[0]["title"]


def test_closed_door_changes_nothing(monkeypatch, client, redis_c):
    client.post(f"/tenants/{T}/inventory/staples", json={"camera_id": CAM})
    inventory.set_count(T, "milk", 3)
    bus.put_latest(T, CAM, pic())
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"inside_fridge_visible": False, "items": {"milk": 0}})
    assert fa.scan_camera(T, CAM, redis_c) == "inside of the fridge not visible"
    assert next(i for i in inventory.list_items(T) if i["name"] == "milk")["count"] == 3


def test_model_is_only_asked_when_the_picture_changed_or_forced(monkeypatch, client, redis_c):
    client.post(f"/tenants/{T}/inventory/staples", json={"camera_id": CAM})
    bus.put_latest(T, CAM, pic(100))
    calls = []
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: calls.append(1) or {"inside_fridge_visible": True, "items": {"milk": 1}})
    t0 = 1_000_000.0
    fa.scan_camera(T, CAM, redis_c, now=t0)
    assert fa.scan_camera(T, CAM, redis_c, now=t0 + 60).startswith("skipped")                      # inside the minimum gap
    assert fa.scan_camera(T, CAM, redis_c, now=t0 + fa.SCAN_SEC + 1).startswith("skipped")         # same picture
    bus.put_latest(T, CAM, pic(200))
    assert not fa.scan_camera(T, CAM, redis_c, now=t0 + fa.SCAN_SEC + 2).startswith("skipped")     # shopping put away
    assert not fa.scan_camera(T, CAM, redis_c, force=True, now=t0 + fa.SCAN_SEC + 3).startswith("skipped")
    assert len(calls) == 3


def test_scan_now_endpoint_queues_a_request(client, redis_c):
    redis_c.delete("invscan:req")
    assert client.post(f"/tenants/{T}/inventory/scan", json={"camera_id": CAM}).json() == {"queued": True}
    assert redis_c.llen("invscan:req") == 1
