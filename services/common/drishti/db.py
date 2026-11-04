"""Postgres access. Multi-tenancy is enforced by row-level security (RLS):

* every tenant-scoped table has `tenant_id` + a policy matching `current_setting('app.tenant_id')`
* FORCE ROW LEVEL SECURITY applies it to the table owner too
* code opens a connection through `_conn(tenant_id)` (scoped) or `_conn(admin=True)` (operator views)
  so a query that forgets a WHERE tenant_id can still never read another tenant's rows.
"""
from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from typing import Iterator

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None

TENANT_TABLES = ("events", "alerts", "actuations", "notes", "site_config", "camera_sources", "inventory", "feedback")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  id         text PRIMARY KEY,
  vertical   text NOT NULL,
  name       text NOT NULL,
  location   text NOT NULL DEFAULT '',
  api_key    text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sop_docs (
  vertical   text PRIMARY KEY,
  text       text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS events (
  frame_id   text PRIMARY KEY,
  tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  camera_id  text NOT NULL,
  level      text NOT NULL,
  decision   text,
  reason     text NOT NULL DEFAULT '',
  rationale  text NOT NULL DEFAULT '',
  objects    jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_tenant_time ON events (tenant_id, created_at DESC);
CREATE TABLE IF NOT EXISTS alerts (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  vertical     text NOT NULL,
  level        text NOT NULL,
  rule         text NOT NULL DEFAULT '',
  reason       text NOT NULL DEFAULT '',
  rationale    text NOT NULL DEFAULT '',
  speak_text   text NOT NULL DEFAULT '',
  objects      jsonb NOT NULL DEFAULT '[]',
  actions      jsonb NOT NULL DEFAULT '[]',
  frame_id     text NOT NULL DEFAULT '',
  acknowledged boolean NOT NULL DEFAULT false,
  false_alarm  boolean NOT NULL DEFAULT false,
  image        bytea,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alerts_tenant_time ON alerts (tenant_id, created_at DESC);
CREATE TABLE IF NOT EXISTS actuations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  alert_id   uuid,
  kind       text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}',
  status     text NOT NULL DEFAULT 'queued',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS actuations_tenant_time ON actuations (tenant_id, created_at DESC);
CREATE TABLE IF NOT EXISTS site_config (
  tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  camera_id  text NOT NULL DEFAULT '',          -- '' = the whole site, otherwise one camera
  data       jsonb NOT NULL DEFAULT '{}',       -- sparse OVERRIDES of the defaults (see siteconfig.py)
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, camera_id)
);
CREATE TABLE IF NOT EXISTS camera_sources (
  tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  camera_id    text NOT NULL,
  scheme       text NOT NULL DEFAULT 'http',     -- http | https | rtsp | rtsps | file
  host         text NOT NULL DEFAULT '',         -- IP or DNS name: the part that changes when the router hands out a new address
  port         integer,
  path         text NOT NULL DEFAULT '/',
  username     text NOT NULL DEFAULT '',
  password_enc text NOT NULL DEFAULT '',         -- Fernet-encrypted (vault.py), never returned by the API
  enabled      boolean NOT NULL DEFAULT true,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, camera_id)
);
CREATE TABLE IF NOT EXISTS inventory (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name              text NOT NULL,
  kind              text NOT NULL DEFAULT 'general',
  camera_id         text,
  qty               integer NOT NULL DEFAULT 0,
  min_stock         integer NOT NULL DEFAULT 1,
  expiry            date,
  expiry_alert_days integer NOT NULL DEFAULT 7,
  last_seen         timestamptz,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);
CREATE TABLE IF NOT EXISTS feedback (               -- operator verdicts on alerts: what the platform learns from
  alert_id   uuid PRIMARY KEY,
  tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  rule       text NOT NULL,
  hour       integer NOT NULL,
  verdict    text NOT NULL CHECK (verdict IN ('false_alarm', 'confirmed')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feedback_tenant_rule ON feedback (tenant_id, rule, created_at DESC);
CREATE TABLE IF NOT EXISTS notes (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  text NOT NULL,
  value      jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
"""

_POLICY = (
    "tenant_id = current_setting('app.tenant_id', true) "
    "OR current_setting('app.admin', true) = 'on'"
)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url, min_size=1, max_size=10, open=True,
            kwargs={"row_factory": dict_row}, timeout=15,
        )
    return _pool


@contextmanager
def _conn(tenant_id: str | None = None, admin: bool = False) -> Iterator:
    """Transaction-scoped connection with the RLS context set. Commits on success."""
    if tenant_id is None and not admin:
        raise ValueError("tenant_id or admin=True is required")
    with pool().connection() as conn:
        conn.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.admin', %s, true)",
            (tenant_id or "", "on" if admin else "off"),
        )
        yield conn


def init_schema() -> None:
    with pool().connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(727001)")  # several gateways may start together
        conn.execute(SCHEMA)
        for t in TENANT_TABLES:
            conn.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            conn.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
            conn.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({_POLICY}) WITH CHECK ({_POLICY})")


def ping() -> bool:
    try:
        with pool().connection() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ tenants
def create_tenant(tenant_id: str, vertical: str, name: str, location: str) -> dict:
    with _conn(admin=True) as c:
        row = c.execute(
            "INSERT INTO tenants (id, vertical, name, location, api_key) VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (tenant_id, vertical, name, location, secrets.token_urlsafe(24)),
        ).fetchone()
    return row


def get_tenant(tenant_id: str) -> dict | None:
    with _conn(admin=True) as c:
        return c.execute("SELECT * FROM tenants WHERE id=%s", (tenant_id,)).fetchone()


def all_tenants() -> list[dict]:
    with _conn(admin=True) as c:
        return c.execute("SELECT * FROM tenants ORDER BY created_at, id").fetchall()


def tenants_with_stats() -> list[dict]:
    with _conn(admin=True) as c:
        return c.execute(
            """
            SELECT t.id, t.vertical, t.name, t.location, t.created_at,
                   (SELECT count(*) FROM alerts a WHERE a.tenant_id=t.id AND a.created_at > now() - interval '24 hours')::int AS alerts_24h,
                   (SELECT count(*) FROM events e WHERE e.tenant_id=t.id AND e.created_at > now() - interval '24 hours')::int AS events_24h,
                   (SELECT max(e.created_at) FROM events e WHERE e.tenant_id=t.id) AS last_seen
            FROM tenants t ORDER BY t.created_at, t.id
            """
        ).fetchall()


def update_tenant(tenant_id: str, name: str | None, location: str | None) -> dict | None:
    with _conn(admin=True) as c:
        return c.execute(
            "UPDATE tenants SET name=COALESCE(%s,name), location=COALESCE(%s,location) WHERE id=%s RETURNING *",
            (name, location, tenant_id),
        ).fetchone()


def set_sop(vertical: str, text: str) -> None:
    """Operator edit (Admin page): replaces the stored SOP for the whole vertical."""
    with _conn(admin=True) as c:
        c.execute(
            """INSERT INTO sop_docs (vertical, text) VALUES (%s,%s)
               ON CONFLICT (vertical) DO UPDATE SET text=EXCLUDED.text, updated_at=now()""", (vertical, text))


# ------------------------------------------------------------------ admin configuration
def get_site_config(tenant_id: str, camera_id: str = "") -> dict:
    with _conn(admin=True) as c:
        row = c.execute("SELECT data FROM site_config WHERE tenant_id=%s AND camera_id=%s", (tenant_id, camera_id)).fetchone()
    return row["data"] if row else {}


def list_site_config_scopes(tenant_id: str) -> list[str]:
    with _conn(admin=True) as c:
        return [r["camera_id"] for r in c.execute("SELECT camera_id FROM site_config WHERE tenant_id=%s", (tenant_id,)).fetchall()]


def set_site_config(tenant_id: str, camera_id: str, data: dict) -> None:
    with _conn(admin=True) as c:
        c.execute(
            """INSERT INTO site_config (tenant_id, camera_id, data) VALUES (%s,%s,%s)
               ON CONFLICT (tenant_id, camera_id) DO UPDATE SET data=EXCLUDED.data, updated_at=now()""",
            (tenant_id, camera_id, Jsonb(data)),
        )


def delete_site_config(tenant_id: str, camera_id: str = "") -> int:
    with _conn(admin=True) as c:
        return c.execute("DELETE FROM site_config WHERE tenant_id=%s AND camera_id=%s", (tenant_id, camera_id)).rowcount


def list_camera_sources(tenant_id: str | None = None) -> list[dict]:
    with _conn(admin=True) as c:
        if tenant_id:
            return c.execute("""SELECT s.*, t.vertical FROM camera_sources s JOIN tenants t ON t.id = s.tenant_id
                                WHERE s.tenant_id=%s ORDER BY s.camera_id""", (tenant_id,)).fetchall()
        return c.execute("""SELECT s.*, t.vertical FROM camera_sources s JOIN tenants t ON t.id = s.tenant_id
                            ORDER BY s.tenant_id, s.camera_id""").fetchall()


def get_camera_source(tenant_id: str, camera_id: str) -> dict | None:
    with _conn(admin=True) as c:
        return c.execute("SELECT * FROM camera_sources WHERE tenant_id=%s AND camera_id=%s", (tenant_id, camera_id)).fetchone()


def upsert_camera_source(tenant_id: str, camera_id: str, scheme: str, host: str, port: int | None, path: str,
                         username: str, password_enc: str | None, enabled: bool) -> None:
    """password_enc=None keeps the stored password; '' clears it."""
    with _conn(admin=True) as c:
        c.execute(
            """INSERT INTO camera_sources (tenant_id, camera_id, scheme, host, port, path, username, password_enc, enabled)
               VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,''),%s)
               ON CONFLICT (tenant_id, camera_id) DO UPDATE SET scheme=EXCLUDED.scheme, host=EXCLUDED.host, port=EXCLUDED.port,
                 path=EXCLUDED.path, username=EXCLUDED.username, enabled=EXCLUDED.enabled, updated_at=now(),
                 password_enc=CASE WHEN %s::text IS NULL THEN camera_sources.password_enc ELSE EXCLUDED.password_enc END""",
            (tenant_id, camera_id, scheme, host, port, path, username, password_enc, enabled, password_enc),
        )


def delete_camera_source(tenant_id: str, camera_id: str) -> int:
    with _conn(admin=True) as c:
        return c.execute("DELETE FROM camera_sources WHERE tenant_id=%s AND camera_id=%s", (tenant_id, camera_id)).rowcount


def upsert_sop(vertical: str, text: str) -> None:
    """Seed only: an operator-edited SOP is never overwritten on restart."""
    with _conn(admin=True) as c:
        c.execute(
            "INSERT INTO sop_docs (vertical, text) VALUES (%s,%s) ON CONFLICT (vertical) DO NOTHING",
            (vertical, text),
        )


def get_sop(vertical: str) -> str | None:
    with _conn(admin=True) as c:
        row = c.execute("SELECT text FROM sop_docs WHERE vertical=%s", (vertical,)).fetchone()
    return row["text"] if row else None


# ------------------------------------------------------------------ events / alerts / actuations
def save_event(tenant_id: str, frame_id: str, camera_id: str, level: str, decision: str | None,
               reason: str, rationale: str, objects: list[str], created_at: str | None = None) -> None:
    with _conn(tenant_id) as c:
        c.execute(
            """INSERT INTO events (frame_id, tenant_id, camera_id, level, decision, reason, rationale, objects, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, COALESCE(%s::timestamptz, now()))
               ON CONFLICT (frame_id) DO NOTHING""",
            (frame_id, tenant_id, camera_id, level, decision, reason, rationale, Jsonb(objects), created_at),
        )


def save_alert(tenant_id: str, vertical: str, level: str, rule: str, reason: str, rationale: str,
               speak_text: str, objects: list[str], actions: list[str], frame_id: str,
               image: bytes | None) -> str:
    with _conn(tenant_id) as c:
        row = c.execute(
            """INSERT INTO alerts (tenant_id, vertical, level, rule, reason, rationale, speak_text, objects, actions, frame_id, image)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text AS id""",
            (tenant_id, vertical, level, rule, reason, rationale, speak_text, Jsonb(objects), Jsonb(actions), frame_id, image),
        ).fetchone()
    return row["id"]


def save_actuation(tenant_id: str, alert_id: str | None, kind: str, payload: dict, status: str = "queued") -> str:
    with _conn(tenant_id) as c:
        row = c.execute(
            "INSERT INTO actuations (tenant_id, alert_id, kind, payload, status) VALUES (%s,%s,%s,%s,%s) RETURNING id::text AS id",
            (tenant_id, alert_id, kind, Jsonb(payload), status),
        ).fetchone()
    return row["id"]


def set_actuation_status(ids: list[str], tenant_id: str, status: str) -> None:
    with _conn(tenant_id) as c:
        c.execute("UPDATE actuations SET status=%s WHERE tenant_id=%s AND id = ANY(%s::uuid[])", (status, tenant_id, ids))


_ALERT_COLS = """id::text AS id, tenant_id, vertical, level, rule, reason, rationale, speak_text, objects, actions,
                 frame_id, created_at, acknowledged, (image IS NOT NULL) AS has_image"""


def list_alerts(tenant_id: str, limit: int) -> list[dict]:
    with _conn(tenant_id) as c:
        return c.execute(
            f"SELECT {_ALERT_COLS} FROM alerts WHERE tenant_id=%s ORDER BY created_at DESC LIMIT %s", (tenant_id, limit)
        ).fetchall()


def list_events(tenant_id: str, limit: int) -> list[dict]:
    with _conn(tenant_id) as c:
        return c.execute(
            """SELECT frame_id, camera_id, level, decision, reason, rationale, objects, created_at
               FROM events WHERE tenant_id=%s ORDER BY created_at DESC LIMIT %s""", (tenant_id, limit)
        ).fetchall()


def list_actuations(tenant_id: str, limit: int) -> list[dict]:
    with _conn(tenant_id) as c:
        return c.execute(
            """SELECT id::text AS id, alert_id::text AS alert_id, kind, payload, status, created_at
               FROM actuations WHERE tenant_id=%s ORDER BY created_at DESC LIMIT %s""", (tenant_id, limit)
        ).fetchall()


def get_alert(alert_id: str) -> dict | None:
    """Admin lookup (the caller then authorises against the alert's tenant)."""
    with _conn(admin=True) as c:
        return c.execute(f"SELECT {_ALERT_COLS} FROM alerts WHERE id=%s::uuid", (alert_id,)).fetchone()


def get_alert_image(alert_id: str) -> bytes | None:
    with _conn(admin=True) as c:
        row = c.execute("SELECT image FROM alerts WHERE id=%s::uuid", (alert_id,)).fetchone()
    return bytes(row["image"]) if row and row["image"] else None


def get_alert_image_by_frame(tenant_id: str, frame_id: str) -> bytes | None:
    with _conn(tenant_id) as c:
        row = c.execute("SELECT image FROM alerts WHERE tenant_id=%s AND frame_id=%s AND image IS NOT NULL LIMIT 1",
                        (tenant_id, frame_id)).fetchone()
    return bytes(row["image"]) if row and row["image"] else None


def ack_alert(alert_id: str, tenant_id: str, false_alarm: bool) -> None:
    with _conn(tenant_id) as c:
        c.execute(
            "UPDATE alerts SET acknowledged=true, false_alarm=%s WHERE id=%s::uuid AND tenant_id=%s",
            (false_alarm, alert_id, tenant_id),
        )


def save_feedback(tenant_id: str, alert_id: str, rule: str, hour: int, verdict: str) -> None:
    """One verdict per alert (the operator may change their mind: the newest click wins)."""
    with _conn(tenant_id) as c:
        c.execute(
            """INSERT INTO feedback (alert_id, tenant_id, rule, hour, verdict) VALUES (%s::uuid, %s, %s, %s, %s)
               ON CONFLICT (alert_id) DO UPDATE SET verdict=EXCLUDED.verdict, created_at=now()""",
            (alert_id, tenant_id, rule, hour, verdict),
        )


def feedback_rows(tenant_id: str, days: int = 60) -> list[dict]:
    with _conn(tenant_id) as c:
        return c.execute(
            """SELECT rule, hour, verdict, created_at FROM feedback
               WHERE tenant_id=%s AND created_at > now() - make_interval(days => %s)""", (tenant_id, days)
        ).fetchall()


def vertical_stats(vertical: str) -> dict:
    with _conn(admin=True) as c:
        tenants = c.execute("SELECT count(*)::int AS n FROM tenants WHERE vertical=%s", (vertical,)).fetchone()["n"]
        lv = c.execute(
            """SELECT e.level, count(*)::int AS n FROM events e JOIN tenants t ON t.id=e.tenant_id
               WHERE t.vertical=%s AND e.created_at > now() - interval '24 hours' GROUP BY e.level""",
            (vertical,),
        ).fetchall()
        alerts = c.execute(
            """SELECT count(*)::int AS n, count(*) FILTER (WHERE level='CRITICAL')::int AS crit
               FROM alerts WHERE vertical=%s AND created_at > now() - interval '24 hours'""",
            (vertical,),
        ).fetchone()
    by = {"NORMAL": 0, "ALERT": 0, "CRITICAL": 0, **{r["level"]: r["n"] for r in lv}}
    return {
        "vertical": vertical, "tenants": tenants, "events_24h": sum(by.values()),
        "alerts_24h": alerts["n"], "critical_24h": alerts["crit"], "by_level": by,
    }


def prune(days: int) -> None:
    """Retention: drop old events and alert images (keeps alert rows for audit)."""
    with _conn(admin=True) as c:
        c.execute("DELETE FROM events WHERE created_at < now() - make_interval(days => %s)", (days,))
        c.execute("DELETE FROM actuations WHERE created_at < now() - make_interval(days => %s)", (days * 4,))
        c.execute("UPDATE alerts SET image=NULL WHERE image IS NOT NULL AND created_at < now() - make_interval(days => %s)", (days * 4,))


# ------------------------------------------------------------------ operator notes (architecture page)
def notes_list() -> list[dict]:
    with _conn(admin=True) as c:
        rows = c.execute("SELECT id::text AS id, tenant_id, value, created_at FROM notes ORDER BY created_at DESC LIMIT 200").fetchall()
    return rows


def notes_create(tenant_id: str, value: dict) -> dict:
    with _conn(admin=True) as c:
        return c.execute(
            "INSERT INTO notes (tenant_id, value) VALUES (%s,%s) RETURNING id::text AS id, tenant_id, value, created_at",
            (tenant_id, Jsonb(value)),
        ).fetchone()


def notes_delete(note_id: str) -> None:
    with _conn(admin=True) as c:
        c.execute("DELETE FROM notes WHERE id=%s::uuid", (note_id,))
