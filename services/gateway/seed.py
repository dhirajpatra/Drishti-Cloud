"""Bootstrap: SOP docs, graph schema, optional demo tenants."""
from __future__ import annotations

from drishti import db, graph, inventory
from drishti.config import settings
from drishti.log import get_logger
from drishti.verticals import VERTICALS, sop_for

log = get_logger("seed")

DEMO_TENANTS = [
    # id, vertical, name, location
    ("mall_1", "mall_security", "Phoenix Mall - Gate A", "Bengaluru"),
    ("home_1_bangalore", "home_seniors", "Patra Home - Bengaluru", "Bengaluru"),
    ("school_1", "school_children", "Greenfield School", "Pune"),
    ("icu_ward_1", "icu_hospital", "City Hospital - ICU Ward 1", "Delhi"),
]

# (person, role) and habits: desc, rule it explains, start_hour <= hour < end_hour
DEMO_GRAPH = {
    "mall_1": {
        "people": [("Ravi", "security_guard")],
        "habits": [("Cleaning crew parks trolleys and bags 06:00-07:00", "unattended_bag", 6, 7)],
    },
    "home_1_bangalore": {
        "people": [("Dad", "senior"), ("Mom", "senior")],
        "habits": [
            ("Dad leaves for his morning walk 04:00-06:00 (front door opens)", "door_open_night", 4, 6),
            ("Afternoon nap 14:00-16:00 - very little motion", "no_motion_2h", 14, 16),
        ],
    },
    "school_1": {
        "people": [("Mrs. Rao", "teacher")],
        "habits": [("Morning assembly: gate stays open 09:00-10:00 for late arrivals", "gate_open_class_time", 9, 10)],
    },
    "icu_ward_1": {
        "people": [("Nurse Priya", "staff")],
        "habits": [],
    },
}


# (item, kind, minimum stock): listed in the Stock panel; counts fill in from what the camera sees
DEMO_INVENTORY = {
    "home_1_bangalore": [("milk", "fridge", 2), ("egg", "fridge", 4)],
    "icu_ward_1": [("paracetamol", "medicine", 5), ("saline", "medicine", 3)],
}


def register_in_graph(t: dict) -> None:
    v = VERTICALS[t["vertical"]]
    graph.upsert_tenant(t["id"], v["tenant_type"], t.get("location", ""), t["vertical"], sop_for(t["vertical"]))
    graph.add_camera(t["id"], "cam_1")


def run() -> None:
    for vid in VERTICALS:
        db.upsert_sop(vid, sop_for(vid))
    graph.ensure_schema()
    existing = {t["id"]: t for t in db.all_tenants()}
    for t in existing.values():
        register_in_graph(t)  # keeps Neo4j in sync after a graph volume reset
    if not settings.seed_demo:
        return
    for tid, vertical, name, location in DEMO_TENANTS:
        if tid in existing:
            continue
        t = db.create_tenant(tid, vertical, name, location)
        register_in_graph(t)
        log.info("seeded demo tenant %s (%s) api_key=%s", tid, vertical, t["api_key"])
        for person, role in DEMO_GRAPH[tid]["people"]:
            graph.add_person(tid, person, role)
        for desc, rule, start, end in DEMO_GRAPH[tid]["habits"]:
            graph.add_habit(tid, DEMO_GRAPH[tid]["people"][0][0], desc, rule=rule, start_hour=start, end_hour=end)
        for item, kind, min_stock in DEMO_INVENTORY.get(tid, []):
            inventory.upsert_item(tid, item, kind=kind, min_stock=min_stock)
