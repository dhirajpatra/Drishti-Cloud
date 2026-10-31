#!/usr/bin/env python3
"""Apply the stock-tracking update to the existing Drishti files.

    cd Drishti-Cloud
    python apply_inventory_patch.py            # patches in place
    python apply_inventory_patch.py --dry-run  # only check that every anchor is found

Required patches are all-or-nothing: if any anchor is missing or ambiguous nothing is written.
Optional patches (architecture page wording) are applied when found and reported otherwise.
"""
import pathlib
import sys

R = True   # required
O = False  # optional

PATCHES = [
    # ---------------------------------------------------------------- database: table + row-level security
    ("services/common/drishti/db.py", R,
     'TENANT_TABLES = ("events", "alerts", "actuations", "notes")',
     'TENANT_TABLES = ("events", "alerts", "actuations", "notes", "inventory")'),
    ("services/common/drishti/db.py", R,
     "CREATE TABLE IF NOT EXISTS notes (",
     """CREATE TABLE IF NOT EXISTS inventory (
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
CREATE TABLE IF NOT EXISTS notes ("""),

    # ---------------------------------------------------------------- gateway: stock API
    ("services/gateway/main.py", R,
     "from drishti import bus, db, graph\n",
     "from drishti import bus, db, graph, inventory\n"),
    ("services/gateway/main.py", R,
     '@app.get("/usage")\n',
     '''# --------------------------------------------------------------------------- stock (fridge / medicine / shelf)
class InventoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: str = "general"
    camera_id: str | None = Field(default=None, max_length=40)
    min_stock: int = Field(default=1, ge=0, le=100000)
    count: int | None = Field(default=None, ge=0, le=100000)      # optional manual correction of the seen count
    expiry: date | None = None
    expiry_alert_days: int = Field(default=7, ge=0, le=365)


@app.get("/tenants/{tenant_id}/inventory")
def list_inventory(tenant_id: str, request: Request):
    """Tracked items with their last seen count, minimum, expiry and a status (ok / low / expiring / expired)."""
    require_tenant(tenant_id, request)
    return inventory.annotate(inventory.list_items(tenant_id))


@app.post("/tenants/{tenant_id}/inventory", status_code=201)
def save_inventory(tenant_id: str, body: InventoryIn, request: Request):
    """Create or update a tracked item. The segmentation agent starts looking for it on the next analysed frame."""
    require_tenant(tenant_id, request)
    name = inventory.norm(body.name)
    if not inventory.NAME_RE.match(name):
        raise HTTPException(422, "name must be 1-40 chars of letters, digits, spaces or underscores")
    if body.kind not in inventory.KINDS:
        raise HTTPException(422, f"kind must be one of {list(inventory.KINDS)}")
    if body.camera_id and not re.fullmatch(r"[A-Za-z0-9_\\-]{1,40}", body.camera_id):
        raise HTTPException(422, "camera_id must be 1-40 chars of [A-Za-z0-9_-]")
    row = inventory.upsert_item(tenant_id, name, body.kind, body.camera_id or None, body.min_stock, body.expiry,
                                body.expiry_alert_days, body.count)
    return inventory.annotate([row])[0]


@app.delete("/tenants/{tenant_id}/inventory/{name}")
def delete_inventory(tenant_id: str, name: str, request: Request):
    require_tenant(tenant_id, request)
    if not inventory.delete_item(tenant_id, inventory.norm(name)):
        raise HTTPException(404, "no such item")
    return {"ok": True}


@app.get("/usage")
'''),

    # ---------------------------------------------------------------- action agent: counts + expiry sweep
    ("services/action-agent/main.py", R,
     "from drishti import bus, db, graph\n",
     "from drishti import bus, db, graph, inventory\n"),
    ("services/action-agent/main.py", R,
     '    if not p.get("static") or level != "NORMAL":\n        try:\n            graph.record_event(',
     '    if not p.get("static"):\n'
     '        try:\n'
     '            inventory.observe(tid, p.get("camera_id", "cam_1"), p.get("objects") or [], r)\n'
     '        except Exception:  # noqa: BLE001 - stock counting must never block the alert path\n'
     '            log.exception("inventory update failed")\n\n'
     '    if not p.get("static") or level != "NORMAL":\n        try:\n            graph.record_event('),
    ("services/action-agent/main.py", R,
     "            camera_watchdog_once(r)\n        except Exception:",
     "            camera_watchdog_once(r)\n            inventory.sweep(r)\n        except Exception:"),

    # ---------------------------------------------------------------- segmenter: also look for tracked items
    ("services/sam3-segmenter/main.py", R,
     "from drishti import bus, llm, simulate\n",
     "from drishti import bus, inventory, llm, simulate\n"),
    ("services/sam3-segmenter/main.py", R,
     "SAM_OFF_TTL = 3600\n",
     '''SAM_OFF_TTL = 3600


def inventory_labels(tid: str) -> list[str]:
    """Stock items this tenant tracks (fridge / medicine shelf): SAM is asked to find them in addition to the vertical's labels."""
    try:
        return [n.replace("_", " ") for n in inventory.tracked_labels(tid)][:30]
    except Exception:  # noqa: BLE001 - never block perception on a database hiccup
        return []
'''),
    ("services/sam3-segmenter/main.py", R,
     'def sam_segment(jpeg: bytes, vertical: str, r) -> list[dict]:\n    labels = ", ".join(VERTICALS[vertical]["sam_labels"])',
     'def sam_segment(jpeg: bytes, vertical: str, r, extra_labels=()) -> list[dict]:\n    labels = ", ".join([*VERTICALS[vertical]["sam_labels"], *extra_labels])'),
    ("services/sam3-segmenter/main.py", R,
     'objects = sam_segment(jpeg, p["vertical"], r)',
     'objects = sam_segment(jpeg, p["vertical"], r, extra_labels=inventory_labels(tid))'),

    # ---------------------------------------------------------------- demo data
    ("services/gateway/seed.py", R,
     "from drishti import db, graph\n",
     "from drishti import db, graph, inventory\n"),
    ("services/gateway/seed.py", R,
     "def register_in_graph(t: dict) -> None:",
     '''# (item, kind, minimum stock): listed in the Stock panel; counts fill in from what the camera sees
DEMO_INVENTORY = {
    "home_1_bangalore": [("milk", "fridge", 2), ("egg", "fridge", 4)],
    "icu_ward_1": [("paracetamol", "medicine", 5), ("saline", "medicine", 3)],
}


def register_in_graph(t: dict) -> None:'''),
    ("services/gateway/seed.py", R,
     '            graph.add_habit(tid, DEMO_GRAPH[tid]["people"][0][0], desc, rule=rule, start_hour=start, end_hour=end)\n',
     '            graph.add_habit(tid, DEMO_GRAPH[tid]["people"][0][0], desc, rule=rule, start_hour=start, end_hour=end)\n'
     '        for item, kind, min_stock in DEMO_INVENTORY.get(tid, []):\n'
     '            inventory.upsert_item(tid, item, kind=kind, min_stock=min_stock)\n'),
    ("services/pi-edge-simulator/main.py", R,
     '        ("quiet_room", {"pir": False, "us_door": 30}, {"objects": [], "hour": 11}),\n',
     '        ("fridge_running_low", {"pir": True}, {"objects": [{"label": "milk", "count": 1}, {"label": "egg", "count": 2}], "hour": 9}),\n'
     '        ("quiet_room", {"pir": False, "us_door": 30}, {"objects": [], "hour": 11}),\n'),

    # ---------------------------------------------------------------- dashboard
    ("services/frontend/src/api.ts", R,
     "export interface CameraInfo { camera_id: string; updated_at: string | null }\n",
     "export interface CameraInfo { camera_id: string; updated_at: string | null }\n"
     "export interface InventoryItem {\n"
     "  id: string; name: string; kind: string; camera_id: string | null; count: number; min_stock: number;\n"
     "  expiry: string | null; expiry_alert_days: number; last_seen: string | null;\n"
     '  status: "ok" | "low" | "expiring" | "expired"; days_to_expiry: number | null;\n'
     "}\n"
     "export interface InventoryInput {\n"
     "  name: string; kind: string; min_stock: number; expiry?: string | null; camera_id?: string | null;\n"
     "  count?: number | null; expiry_alert_days?: number;\n"
     "}\n"),
    ("services/frontend/src/api.ts", R,
     "  ingest: (tenantId: string, file: File,",
     "  inventory: (id: string) => json<InventoryItem[]>(`/tenants/${encodeURIComponent(id)}/inventory`),\n"
     "  saveInventory: (id: string, b: InventoryInput) =>\n"
     "    json<InventoryItem>(`/tenants/${encodeURIComponent(id)}/inventory`, jsonInit(\"POST\", b)),\n"
     "  removeInventory: (id: string, name: string) =>\n"
     "    json<{ ok: true }>(`/tenants/${encodeURIComponent(id)}/inventory/${encodeURIComponent(name)}`, { method: \"DELETE\" }),\n"
     "  ingest: (tenantId: string, file: File,"),
    ("services/frontend/src/pages/Dashboard.tsx", R,
     'import { FeedSources } from "../components/FeedSources";\n',
     'import { FeedSources } from "../components/FeedSources";\nimport { Inventory } from "../components/Inventory";\n'),
    ("services/frontend/src/pages/Dashboard.tsx", R,
     "            <FeedSources key={`f-${tenant.id}`} tenantId={tenant.id} />\n",
     "            <FeedSources key={`f-${tenant.id}`} tenantId={tenant.id} />\n"
     "            <Inventory key={`i-${tenant.id}`} tenantId={tenant.id} />\n"),

    # ---------------------------------------------------------------- architecture page: wording that contradicted the code
    ("services/frontend/src/pages/Architecture.tsx", O,
     '{ id:"memory", name:"Memory Agent", model:"Llama open-weights (local) + Neo4j", color:"#34d399", icon:Database, desc:"5-person face embeddings per home, habit graph, object permanence across occlusions.", io:"in: embedding → out: person_id, confidence, last_seen" }',
     '{ id:"memory", name:"Habit Memory", model:"Neo4j habit graph (no face recognition)", color:"#34d399", icon:Database, desc:"Per-site habit graph: a known habit downgrades a matching alert, and a false alarm teaches a new one.", io:"in: rule + hour → out: habit match / suppress" }'),
    ("services/frontend/src/pages/Architecture.tsx", O,
     "Face embedding → no match in 5-person graph → 'unknown'",
     "Habit graph: no known habit covers this hour (02:14)"),
    ("services/frontend/src/pages/Architecture.tsx", O,
     "Multi-tenant auth — tenant_id from JWT claim on every request",
     "Multi-tenant auth — per-tenant API key or admin token on every request"),
    ("services/frontend/src/pages/Architecture.tsx", O,
     "Eleven services.",
     "Fourteen services."),
    ("services/frontend/src/pages/Architecture.tsx", O,
     "Postgres RLS on tenant_id, MQTT ACLs per device certificate, JWT tenant claim verified at the FastAPI gateway. Each home sees only its timeline, its 5 persons, its habits.",
     "Postgres row-level security on tenant_id and per-tenant API keys checked at the FastAPI gateway. Each site sees only its own timeline, people and habits."),
    ("services/frontend/src/pages/Architecture.tsx", O,
     "Each home is an autonomous edge tenant. Raw video never leaves the house — only a compressed scene-graph JSON crosses MQTT.",
     "Each site is an isolated tenant. Cameras and the Pi edge unit post frames and sensor JSON to the gateway over HTTP; only changed scenes reach a model."),
    ("services/frontend/src/pages/Architecture.tsx", O,
     r"""# enrol a home (max 5 persons):
curl -X POST localhost:8080/tenants/home-01/persons \\
  -H "Authorization: Bearer $JWT" -d '{"name":"Asha"}'""",
     r"""# create a tenant (admin):
curl -X POST localhost:8000/tenants -H "Content-Type: application/json" \\
  -d '{"id":"home-02","vertical":"home_seniors"}'"""),
]


def main() -> int:
    dry = "--dry-run" in sys.argv
    root = pathlib.Path.cwd()
    texts: dict[str, str] = {}
    problems, skipped = [], []
    for path, required, old, new in PATCHES:
        p = root / path
        if path not in texts:
            if not p.exists():
                texts[path] = None
            else:
                texts[path] = p.read_text(encoding="utf-8")
        text = texts[path]
        if text is None:
            (problems if required else skipped).append(f"{path}: file not found")
            continue
        n = text.count(old)
        if n != 1:
            msg = f"{path}: anchor found {n}x (need exactly 1): {old[:70]!r}"
            (problems if required else skipped).append(msg)
            continue
        texts[path] = text.replace(old, new)
    if problems:
        print("NOTHING WRITTEN. Fix these first (is this the repo root, and are the files unmodified?):")
        for m in problems:
            print("  -", m)
        return 1
    if not dry:
        for path, text in texts.items():
            if text is not None:
                (root / path).write_text(text, encoding="utf-8")
    print(("Dry run OK" if dry else "Patched") + f": {len({p for p, t in texts.items() if t is not None})} files.")
    for m in skipped:
        print("  optional, skipped:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
