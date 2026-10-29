"""Container healthcheck for worker agents: `python -m drishti.healthcheck <group>`.
Healthy while the worker heartbeat key exists in Redis (set every loop, expires in 180 s)."""
import sys

from .bus import redis_client

group = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    ok = bool(list(redis_client().scan_iter(f"hb:{group}:*", count=100)))
except Exception:  # noqa: BLE001
    ok = False
sys.exit(0 if ok else 1)
