"""Stock tracking for fridges, medicine trolleys and shelves (multi-tenant, Postgres row-level security).

An operator declares items per tenant (name, minimum stock, optional expiry). From then on:
  * the segmentation agent is also asked to look for those names (`tracked_labels`),
  * the action agent updates each item's count from what the camera saw (`observe`) and raises a dashboard
    notification when the count drops below the minimum,
  * a periodic sweep (`sweep`) raises notifications for items that are expired or about to expire.

Honest limits: counts are only as good as the segmentation (stacked or hidden items are missed), a count is updated only
when at least one tracked item of that camera is visible (a closed fridge door must not read as "everything is gone"),
and expiry dates are entered by an operator, not read from packaging.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from . import bus, db
from .log import get_logger

log = get_logger("inventory")

KINDS = ("fridge", "medicine", "general")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}$")

_COLS = ("id::text AS id, name, kind, camera_id, qty AS count, min_stock, expiry::text AS expiry, "
         "expiry_alert_days, last_seen")


def norm(name: str) -> str:
    """'Egg Tray' -> 'egg_tray' (also how detected labels are compared)."""
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def _row(r: dict) -> dict:
    d = dict(r)
    if d.get("last_seen") is not None:
        d["last_seen"] = d["last_seen"].isoformat()
    return d


# ------------------------------------------------------------------ storage
def list_items(tid: str) -> list[dict]:
    with db._conn(tid) as c:
        rows = c.execute(f"SELECT {_COLS} FROM inventory WHERE tenant_id=%s ORDER BY kind, name", (tid,)).fetchall()
    return [_row(r) for r in rows]


def upsert_item(tid: str, name: str, kind: str = "general", camera_id: str | None = None, min_stock: int = 1,
                expiry: date | None = None, expiry_alert_days: int = 7, count: int | None = None) -> dict:
    """Create or update an item. `count=None` keeps the last observed count (or 0 for a new item)."""
    with db._conn(tid) as c:
        row = c.execute(
            f"""INSERT INTO inventory (tenant_id, name, kind, camera_id, qty, min_stock, expiry, expiry_alert_days)
                VALUES (%s,%s,%s,%s,COALESCE(%s,0),%s,%s,%s)
                ON CONFLICT (tenant_id, name) DO UPDATE SET
                  kind=EXCLUDED.kind, camera_id=EXCLUDED.camera_id, min_stock=EXCLUDED.min_stock,
                  expiry=EXCLUDED.expiry, expiry_alert_days=EXCLUDED.expiry_alert_days,
                  qty=COALESCE(%s, inventory.qty), updated_at=now()
                RETURNING {_COLS}""",
            (tid, name, kind, camera_id, count, min_stock, expiry, expiry_alert_days, count),
        ).fetchone()
    return _row(row)


def delete_item(tid: str, name: str) -> bool:
    with db._conn(tid) as c:
        cur = c.execute("DELETE FROM inventory WHERE tenant_id=%s AND name=%s", (tid, name))
        return cur.rowcount > 0


def set_count(tid: str, name: str, count: int) -> None:
    with db._conn(tid) as c:
        c.execute("UPDATE inventory SET qty=%s, last_seen=now(), updated_at=now() WHERE tenant_id=%s AND name=%s",
                  (count, tid, name))


def tracked_labels(tid: str) -> list[str]:
    return [it["name"] for it in list_items(tid)]


# ------------------------------------------------------------------ status
def status_of(item: dict, today: date | None = None) -> tuple[str, int | None]:
    """('expired' | 'low' | 'expiring' | 'ok', days until expiry or None)."""
    today = today or date.today()
    days = (date.fromisoformat(item["expiry"]) - today).days if item.get("expiry") else None
    if days is not None and days < 0:
        return "expired", days
    if item["count"] < item["min_stock"]:
        return "low", days
    if days is not None and days <= item.get("expiry_alert_days", 7):
        return "expiring", days
    return "ok", days


def annotate(items: list[dict], today: date | None = None) -> list[dict]:
    out = []
    for it in items:
        status, days = status_of(it, today)
        out.append({**it, "status": status, "days_to_expiry": days})
    return out


# ------------------------------------------------------------------ detection -> counts -> notifications
def observe(tid: str, camera_id: str, objects: list[dict], r=None) -> list[str]:
    """Update counts from one analysed frame. Returns the names that were found below their minimum."""
    items = list_items(tid)
    if not items:
        return []
    seen = Counter(norm(o["label"]) for o in objects or [] if isinstance(o, dict) and o.get("label"))
    scoped = [it for it in items if not it["camera_id"] or it["camera_id"] == camera_id]
    if not any(seen.get(it["name"]) for it in scoped):
        return []                                   # the shelf is not in view: unknown is not the same as empty
    low: list[str] = []
    today = date.today().isoformat()
    for it in scoped:
        n = seen.get(it["name"], 0)
        set_count(tid, it["name"], n)
        if n < it["min_stock"]:
            low.append(it["name"])
            level = "CRITICAL" if (it["kind"] == "medicine" and n == 0) else "WARNING"
            bus.notify(tid, "inventory", level, f"{it['name']} is low: {n} seen, minimum {it['min_stock']}",
                       f"Seen on camera {camera_id}. Reorder or restock.", key=f"low:{it['name']}:{today}", r=r)
    return low


def sweep(r=None) -> int:
    """Notify about expired / soon-to-expire items for every tenant. Returns how many notifications were raised."""
    raised = 0
    for t in db.all_tenants():
        for it in annotate(list_items(t["id"])):
            if it["expiry"] is None or it["status"] not in ("expired", "expiring", "low"):
                continue
            days = it["days_to_expiry"]
            if days is None or days > it["expiry_alert_days"]:
                continue
            title = (f"{it['name']} expired on {it['expiry']}" if days < 0
                     else f"{it['name']} expires in {days} day(s) ({it['expiry']})")
            if bus.notify(t["id"], "inventory", "WARNING", title, "Check stock and replace if needed.",
                          key=f"exp:{it['name']}:{it['expiry']}:{'expired' if days < 0 else 'soon'}", r=r):
                raised += 1
    return raised
