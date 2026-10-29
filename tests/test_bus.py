"""Redis bus: per-tenant streams, fair share, retry then dead-letter."""
import threading
import time

from drishti import bus


def test_per_tenant_queues_and_fair_share(redis_c):
    redis_c.flushdb()
    for i in range(5):
        bus.publish("raw_frames", "noisy", {"n": i}, redis_c)
    bus.publish("raw_frames", "quiet", {"n": 0}, redis_c)
    assert redis_c.smembers("streams:raw_frames") == {"noisy", "quiet"}
    seen: list[str] = []
    done = threading.Event()

    def handler(p, r):
        seen.append(p)
        if len(seen) >= 6:
            done.set()
            bus._stop.set()

    bus._stop.clear()
    t = threading.Thread(target=bus.consume, args=("raw_frames", "fair", handler), kwargs={"block_ms": 200}, daemon=True)
    t.start()
    assert done.wait(10)
    t.join(5)
    bus._stop.clear()
    assert {"n": 0} in seen[:3] and len(seen) == 6  # quiet tenant is not starved behind noisy's backlog


def test_failing_message_is_retried_then_dead_lettered(redis_c):
    redis_c.flushdb()
    bus.publish("raw_frames", "t1", {"boom": True}, redis_c)
    calls = {"n": 0}

    def handler(p, r):
        calls["n"] += 1
        raise RuntimeError("poison")

    bus._stop.clear()
    bus.RECLAIM_EVERY_SEC = 0
    t = threading.Thread(target=bus.consume, args=("raw_frames", "dl", handler), kwargs={"block_ms": 100}, daemon=True)
    t.start()
    deadline = time.time() + 15
    while time.time() < deadline and not redis_c.xlen("dead:raw_frames"):
        time.sleep(0.2)
    bus._stop.set()
    t.join(5)
    bus._stop.clear()
    assert redis_c.xlen("dead:raw_frames") == 1 and calls["n"] >= 2


def test_rate_limiter(redis_c):
    redis_c.flushdb()
    assert [bus.allow("rl:x", 2, 60, redis_c) for _ in range(3)] == [True, True, False]
