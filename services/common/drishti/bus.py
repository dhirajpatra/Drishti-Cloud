"""Redis bus: per-tenant streams, consumer groups, frame blobs, rate limiting.

Stream layout (spec: "Redis queue per tenant"):
    queue:{stage}:{tenant_id}      one stream per tenant per pipeline stage
    streams:{stage}                set of tenant ids that have a stream for that stage
Consumers read ONE message per tenant per round -> fair share, a noisy tenant cannot starve the rest.
Every agent is stateless; scale with `docker compose up --scale <agent>=N` (consumer group balances).
"""
from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

import redis

from .config import settings
from .log import get_logger

log = get_logger("bus")

_text: redis.Redis | None = None
_bin: redis.Redis | None = None

RECLAIM_EVERY_SEC = 15  # how often a worker looks for messages a dead sibling left pending
STAGES = ("raw_frames", "segmented", "perceived", "assessed", "decided", "proofed")


def redis_client() -> redis.Redis:
    """Decoded (str) client for JSON, hashes, streams."""
    global _text
    if _text is None:
        _text = redis.Redis.from_url(settings.redis_uri, decode_responses=True, health_check_interval=30)
    return _text


def redis_bin() -> redis.Redis:
    """Raw-bytes client for JPEG blobs."""
    global _bin
    if _bin is None:
        _bin = redis.Redis.from_url(settings.redis_uri, decode_responses=False, health_check_interval=30)
    return _bin


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_hour() -> int:
    """Hour of day in the container's TZ (compose sets TZ)."""
    return datetime.now().astimezone().hour


# ------------------------------------------------------------------ blobs
def put_frame(frame_id: str, jpeg: bytes) -> None:
    redis_bin().set(f"frame:{frame_id}", jpeg, ex=settings.frame_ttl_sec)


def get_frame(frame_id: str) -> bytes | None:
    return redis_bin().get(f"frame:{frame_id}")


def put_proof(frame_id: str, jpeg: bytes) -> None:
    redis_bin().set(f"proof:{frame_id}", jpeg, ex=settings.frame_ttl_sec)


def get_proof(frame_id: str) -> bytes | None:
    return redis_bin().get(f"proof:{frame_id}")


# ------------------------------------------------------------------ rate limit
def allow(key: str, limit: int, window_sec: int, r: redis.Redis | None = None) -> bool:
    """Fixed-window counter. True while under `limit` hits per window."""
    r = r or redis_client()
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window_sec, nx=True)
    count, _ = pipe.execute()
    return int(count) <= limit


# ------------------------------------------------------------------ publish
def publish(stage: str, tenant_id: str, payload: dict, r: redis.Redis | None = None) -> str:
    r = r or redis_client()
    pipe = r.pipeline()
    pipe.xadd(f"queue:{stage}:{tenant_id}", {"d": json.dumps(payload)}, maxlen=settings.stream_maxlen, approximate=True)
    pipe.sadd(f"streams:{stage}", tenant_id)
    return pipe.execute()[0]


# ------------------------------------------------------------------ consume
_stop = threading.Event()


def _install_signals() -> None:
    def _handler(signum, _frame):  # noqa: ANN001
        log.info("signal %s received - draining and stopping", signum)
        _stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except ValueError:  # not main thread
            pass


def stopping() -> bool:
    return _stop.is_set()


def consumer_name() -> str:
    return os.environ.get("HOSTNAME") or socket.gethostname()


def heartbeat(service: str, r: redis.Redis | None = None) -> None:
    (r or redis_client()).set(f"hb:{service}:{consumer_name()}", utcnow_iso(), ex=180)


def _ensure_group(r: redis.Redis, stream: str, group: str) -> None:
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _reclaim(r: redis.Redis, stream: str, group: str, name: str, stage: str) -> list[tuple[str, dict]]:
    """Take over messages another (dead) consumer left pending; dead-letter poison messages."""
    out: list[tuple[str, dict]] = []
    try:
        res = r.xautoclaim(stream, group, name, min_idle_time=settings.claim_idle_ms, start_id="0-0", count=5)
    except redis.ResponseError:
        return out
    for mid, fields in res[1]:
        if not fields:  # trimmed away
            r.xack(stream, group, mid)
            continue
        info = r.xpending_range(stream, group, min=mid, max=mid, count=1)
        delivered = info[0]["times_delivered"] if info else 1
        if delivered > settings.max_attempts:
            log.error("dead-lettering %s on %s after %d attempts", mid, stream, delivered)
            r.xadd(f"dead:{stage}", {"stream": stream, "d": fields.get("d", "")}, maxlen=500, approximate=True)
            r.xack(stream, group, mid)
            continue
        out.append((mid, fields))
    return out


def consume(stage: str, group: str, handler: Callable[[dict, redis.Redis], None], *, block_ms: int = 2000) -> None:
    """Blocking worker loop. handler(payload, redis) must publish its own output.

    A message is acked only after the handler returns; an exception leaves it pending and it is
    retried (via XAUTOCLAIM) up to MAX_ATTEMPTS before going to dead:{stage}.
    """
    _install_signals()
    r = redis_client()
    name = consumer_name()
    known: set[str] = set()
    last_reclaim = 0.0
    log.info("worker up | stage=%s group=%s consumer=%s", stage, group, name)

    while not _stop.is_set():
        try:
            heartbeat(group, r)
            tenants = sorted(r.smembers(f"streams:{stage}"))
            if not tenants:
                time.sleep(1.0)
                continue
            for tid in tenants:
                if tid not in known:
                    _ensure_group(r, f"queue:{stage}:{tid}", group)
                    known.add(tid)

            batch: list[tuple[str, str, dict]] = []  # (stream, id, fields)
            if time.time() - last_reclaim > RECLAIM_EVERY_SEC:
                last_reclaim = time.time()
                for tid in tenants:
                    s = f"queue:{stage}:{tid}"
                    batch += [(s, mid, f) for mid, f in _reclaim(r, s, group, name, stage)]

            resp = r.xreadgroup(group, name, {f"queue:{stage}:{t}": ">" for t in tenants}, count=1, block=block_ms)
            for stream, msgs in resp or []:
                batch += [(stream, mid, f) for mid, f in msgs]

            for stream, mid, fields in batch:
                if _stop.is_set():
                    break  # unacked messages are reclaimed by a sibling
                try:
                    payload = json.loads(fields["d"])
                    handler(payload, r)
                except Exception:  # noqa: BLE001
                    log.exception("handler failed for %s %s - will retry", stream, mid)
                    continue
                r.xack(stream, group, mid)
        except redis.ConnectionError as exc:
            log.warning("redis unavailable (%s) - retrying", exc)
            time.sleep(2)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            time.sleep(1)
    log.info("worker stopped | stage=%s", stage)


def run_periodic(service: str, interval_sec: float, fn: Callable[[], None]) -> None:
    """Loop for agents that are timer-driven rather than queue-driven (news, simulator, ingestor).
    Heartbeats every <=30 s even while sleeping so container healthchecks stay green."""
    _install_signals()
    log.info("periodic worker up | %s every %ss", service, interval_sec)
    while not _stop.is_set():
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("%s tick failed", service)
        waited = 0.0
        while waited < interval_sec and not _stop.is_set():
            try:
                heartbeat(service)
            except Exception:  # noqa: BLE001
                pass
            step = min(30.0, interval_sec - waited)
            _stop.wait(step)
            waited += step
