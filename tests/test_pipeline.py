"""End-to-end: HTTP ingest -> six agents -> Postgres alert + edge actuation, one hazard per vertical."""
import io
import json

import pytest
from PIL import Image, ImageDraw

from conftest import run_pipeline

CASES = [  # tenant, sensors, expected level, expected rule
    ("mall_1", {"pir": True, "_sim": {"hour": 14, "objects": [{"label": "bag", "bbox": [0.4, 0.55, 0.55, 0.8]}],
                                      "elapsed": {"unattended_bag": 150}}}, "ALERT", "unattended_bag"),
    ("home_1_bangalore", {"pir": True, "_sim": {"hour": 14, "objects": [{"label": "fallen_person", "bbox": [0.2, 0.6, 0.7, 0.9]}]}},
     "CRITICAL", None),
    ("school_1", {"pir": True, "_sim": {"hour": 11, "objects": [{"label": "child", "count": 3},
                                                                 {"label": "person", "bbox": [0.7, 0.2, 0.9, 0.9]}]}},
     "CRITICAL", None),
    ("icu_ward_1", {"pir": True, "_sim": {"hour": 14, "ocr": ["HR 132 ALARM"],
                                          "objects": [{"label": "monitor", "bbox": [0.05, 0.1, 0.3, 0.4]}]}},
     "CRITICAL", None),
]


def jpeg(seed: int = 0) -> bytes:
    img = Image.new("RGB", (640, 480), (40 + seed * 20 % 120, 60, 90))
    ImageDraw.Draw(img).rectangle([100 + seed * 30, 100, 300 + seed * 30, 380], fill=(200, 160, 60))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def post(client, tenant, sensors, seed=0, **form):
    return client.post(f"/ingest/{tenant}", files={"file": ("f.jpg", jpeg(seed), "image/jpeg")},
                       data={"sensors": json.dumps(sensors), "force": "true", **form})


@pytest.mark.parametrize("tenant,sensors,level,rule", CASES, ids=[c[0] for c in CASES])
def test_hazard_becomes_alert(client, agents, redis_c, tenant, sensors, level, rule):
    redis_c.flushdb()  # fresh cooldowns / scene hashes
    r = post(client, tenant, sensors)
    assert r.status_code == 200, r.text
    frame_id = r.json()["frame_id"]
    run_pipeline(redis_c, agents)

    ev = next(e for e in client.get(f"/tenants/{tenant}/events").json() if e["frame_id"] == frame_id)
    assert ev["level"] == level
    alerts = client.get(f"/tenants/{tenant}/alerts").json()
    alert = next(a for a in alerts if a["frame_id"] == frame_id)
    assert alert["level"] == level and alert["reason"]
    if rule:
        assert alert["rule"] == rule
    # annotated proof image is attached and served
    img = client.get(f"/alerts/{alert['id']}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    Image.open(io.BytesIO(img.content)).verify()
    # the edge gets commands to speak / show, and they are consumed exactly once
    cmds = client.get(f"/edge/{tenant}/actuate").json()["commands"]
    assert cmds and all("kind" in c and "payload" in c for c in cmds)
    assert client.get(f"/edge/{tenant}/actuate").json()["commands"] == []


def test_normal_frame_makes_no_alert(client, agents, redis_c):
    redis_c.flushdb()
    before = len(client.get("/tenants/mall_1/alerts").json())
    r = post(client, "mall_1", {"pir": True, "_sim": {"objects": [{"label": "person", "count": 4}]}})
    run_pipeline(redis_c, agents)
    ev = next(e for e in client.get("/tenants/mall_1/events").json() if e["frame_id"] == r.json()["frame_id"])
    assert ev["level"] == "NORMAL"
    assert len(client.get("/tenants/mall_1/alerts").json()) == before


def test_alert_cooldown_dedupes_repeats(client, agents, redis_c):
    redis_c.flushdb()
    sensors = CASES[0][1]
    for i in range(2):
        post(client, "mall_1", sensors, seed=i)
    run_pipeline(redis_c, agents)
    frames = [e["frame_id"] for e in client.get("/tenants/mall_1/events").json()]
    alerts = [a for a in client.get("/tenants/mall_1/alerts").json() if a["frame_id"] in frames[:2]]
    assert len(alerts) == 1, "second identical alert inside ALERT_COOLDOWN_SEC must be suppressed"


def test_known_habit_suppresses_alert(client, agents, redis_c, fake_graph):
    """Wonder + graph memory: a bag at 06:30 matches the cleaning-crew habit -> NORMAL, no alert."""
    redis_c.flushdb()
    sensors = {"pir": True, "_sim": {"hour": 6, "objects": [{"label": "bag", "bbox": [0.4, 0.55, 0.55, 0.8]}],
                                     "elapsed": {"unattended_bag": 150}}}
    r = post(client, "mall_1", sensors)
    run_pipeline(redis_c, agents)
    fid = r.json()["frame_id"]
    ev = next(e for e in client.get("/tenants/mall_1/events").json() if e["frame_id"] == fid)
    assert ev["level"] == "NORMAL" and ev["decision"] == "SUPPRESSED_HABIT"
    assert not [a for a in client.get("/tenants/mall_1/alerts").json() if a["frame_id"] == fid]


def test_news_escalates_alert(client, agents, redis_c, fake_graph):
    redis_c.flushdb()
    fake_graph.add_news("mall_1", "Bomb threat reported near Bengaluru mall", "test", None, "2026-10-28T00:00:00+00:00")
    r = post(client, "mall_1", CASES[0][1])
    run_pipeline(redis_c, agents)
    ev = next(e for e in client.get("/tenants/mall_1/events").json() if e["frame_id"] == r.json()["frame_id"])
    assert ev["level"] == "CRITICAL" and ev["decision"] == "ESCALATED_NEWS"
    fake_graph._news["mall_1"].clear()


def test_false_alarm_ack_teaches_habit(client, agents, redis_c, fake_graph):
    redis_c.flushdb()
    r = post(client, "mall_1", CASES[0][1])
    run_pipeline(redis_c, agents)
    alert = next(a for a in client.get("/tenants/mall_1/alerts").json() if a["frame_id"] == r.json()["frame_id"])
    n = len(fake_graph.habits("mall_1"))
    assert client.post(f"/alerts/{alert['id']}/ack", json={"false_alarm": True}).json() == {"ok": True}
    assert len(fake_graph.habits("mall_1")) == n + 1
