"""Test harness: real Redis + real Postgres (RLS is part of what we test), in-memory graph, MOCK_MODE.

Needs `redis-server` and Postgres binaries (initdb/pg_ctl) on the machine. No Docker, no Neo4j, no API key.
"""
from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES / "common"))
sys.path.insert(0, str(SERVICES / "gateway"))
sys.path.insert(0, str(Path(__file__).parent))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pg_bin() -> str | None:
    for d in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if os.path.exists(f"{d}/initdb"):
            return d
    p = shutil.which("initdb")
    return os.path.dirname(p) if p else None


REDIS_PORT, PG_PORT = _free_port(), _free_port()

# Settings are read at import time -> set the environment BEFORE anything imports `drishti`.
os.environ.update({
    "REDIS_URI": f"redis://127.0.0.1:{REDIS_PORT}/0",
    "DATABASE_URL": f"postgresql://drishti:drishti123@127.0.0.1:{PG_PORT}/drishti",
    "LLAMA_API_KEY": "", "MOCK_MODE": "true", "LOW_COST_MODE": "true", "AUTH_REQUIRED": "false",
    "ADMIN_TOKEN": "", "SEED_DEMO": "true", "MAX_FRAMES_PER_MIN": "1000", "ALERT_COOLDOWN_SEC": "300",
    "TZ": "Asia/Kolkata", "NOTIFY_WEBHOOK_URL": "", "NEWS_API_KEY": "", "MAX_ATTEMPTS": "2", "CLAIM_IDLE_MS": "100",
})


@pytest.fixture(scope="session", autouse=True)
def infra():
    if not shutil.which("redis-server") or not _pg_bin():
        pytest.skip("redis-server and postgres binaries are required for the integration tests")
    tmp = Path(tempfile.mkdtemp(prefix="drishti-test-"))
    procs: list[subprocess.Popen] = []

    redis_p = subprocess.Popen(["redis-server", "--port", str(REDIS_PORT), "--save", "", "--appendonly", "no",
                                "--bind", "127.0.0.1", "--dir", str(tmp)], stdout=subprocess.DEVNULL)
    procs.append(redis_p)

    pgbin, data = _pg_bin(), tmp / "pg"
    as_user: list[str] = []
    if os.geteuid() == 0:  # postgres refuses to run as root
        import pwd
        pw = pwd.getpwnam("postgres")
        os.chmod(tmp, 0o755)
        data.mkdir()
        os.chown(data, pw.pw_uid, pw.pw_gid)
        as_user = ["runuser", "-u", "postgres", "--"]
    subprocess.run([*as_user, f"{pgbin}/initdb", "-D", str(data), "-A", "trust", "-U", "postgres", "-E", "UTF8"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([*as_user, f"{pgbin}/pg_ctl", "-D", str(data), "-w", "-o",
                    f"-p {PG_PORT} -k {data} -c listen_addresses=127.0.0.1", "start", "-l", str(data / "pg.log")],
                   check=True, stdout=subprocess.DEVNULL)
    # superuser role named drishti: FORCE RLS still applies to it unless BYPASSRLS, which superusers have -> use a plain role
    for sql in ("CREATE ROLE drishti LOGIN PASSWORD 'drishti123'", "CREATE DATABASE drishti OWNER drishti"):
        subprocess.run([*as_user, f"{pgbin}/psql", "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres", "-c", sql],
                       check=True, stdout=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", REDIS_PORT), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield
    try:
        from drishti import db
        if db._pool is not None:
            db._pool.close()
    except Exception:  # noqa: BLE001
        pass
    subprocess.run([*as_user, f"{pgbin}/pg_ctl", "-D", str(data), "-m", "immediate", "stop"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for p in procs:
        p.terminate()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def fake_graph(infra):
    from drishti import graph
    from fakegraph import FakeGraph
    fg = FakeGraph()
    for name in ("ping", "ensure_schema", "prune", "upsert_tenant", "add_camera", "add_person", "add_habit",
                 "record_event", "add_news", "habits", "recent_events", "news", "subgraph"):
        setattr(graph, name, getattr(fg, name))
    return fg


def load_agent(name: str):
    """Import services/<name>/main.py under a unique module name (every agent file is called main.py)."""
    spec = importlib.util.spec_from_file_location(f"agent_{name.replace('-', '_')}", SERVICES / name / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def agents(infra, fake_graph):
    return {n: load_agent(n) for n in ("sam3-segmenter", "ocr-reader", "spark-multimodal", "wonder-reasoner",
                                       "image-alert", "action-agent")}


@pytest.fixture(scope="session")
def client(infra, fake_graph):
    from fastapi.testclient import TestClient
    import main as gateway  # services/gateway/main.py
    with TestClient(gateway.app) as c:  # runs lifespan: schema + seed
        yield c


@pytest.fixture()
def redis_c(infra):
    from drishti import bus
    return bus.redis_client()


def drain(redis_c, stage: str, handler) -> int:
    """Run every queued message of a stage through an agent's handler (what bus.consume does, minus the loop)."""
    n = 0
    for tid in sorted(redis_c.smembers(f"streams:{stage}")):
        key = f"queue:{stage}:{tid}"
        for mid, fields in redis_c.xrange(key):
            import json
            handler(json.loads(fields["d"]), redis_c)
            redis_c.xdel(key, mid)
            n += 1
    return n


def run_pipeline(redis_c, agents) -> None:
    for stage, agent in (("raw_frames", "sam3-segmenter"), ("segmented", "ocr-reader"), ("perceived", "spark-multimodal"),
                         ("assessed", "wonder-reasoner"), ("decided", "image-alert"), ("proofed", "action-agent")):
        drain(redis_c, stage, agents[agent].handle)
