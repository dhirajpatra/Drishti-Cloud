"""A silent camera raises one notification, and one more when it comes back."""
import importlib.util
import pathlib
import time
from datetime import datetime, timezone

from drishti import bus
from drishti.config import settings

spec = importlib.util.spec_from_file_location("action_agent_w", pathlib.Path(__file__).parents[1] / "services/action-agent/main.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)


def test_offline_then_back(client, redis_c, monkeypatch):
    redis_c.flushdb()
    monkeypatch.setattr(settings, "camera_offline_sec", 600)
    redis_c.set("latestts:mall_1:door", datetime.fromtimestamp(time.time() - 1800, timezone.utc).isoformat())
    assert aa.camera_watchdog_once(redis_c) == 1
    assert aa.camera_watchdog_once(redis_c) == 0                                  # once, not every minute
    n = bus.notifications("mall_1")
    assert n[0]["kind"] == "camera" and n[0]["level"] == "WARNING" and "door" in n[0]["title"]
    redis_c.set("latestts:mall_1:door", datetime.now(timezone.utc).isoformat())
    assert aa.camera_watchdog_once(redis_c) == 1
    assert "back online" in bus.notifications("mall_1")[0]["title"]
    assert aa.camera_watchdog_once(redis_c) == 0
