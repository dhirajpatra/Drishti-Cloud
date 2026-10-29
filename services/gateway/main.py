"""Drishti gateway - FastAPI front door.

* multi-tenant auth (per-tenant API key for edges, admin token for operators)
* frame ingest: validate -> normalise -> perceptual-hash dedupe -> Redis stream
* read API for the dashboard (alerts, events, graph, stats, usage)
* edge actuation polling (what the Pi should say / show / sound)
"""
from __future__ import annotations

import hmac
import io
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from drishti import bus, db, graph
from drishti.config import settings
from drishti.log import get_logger
from drishti.verticals import VERTICALS, sop_for
import seed

log = get_logger("gateway")

TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,62}$")
MAX_SENSORS_BYTES = 8192
_tenant_cache: dict[str, tuple[float, dict]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auth_required and not settings.admin_token:
        raise RuntimeError("AUTH_REQUIRED=true needs ADMIN_TOKEN to be set")
    for attempt in range(30):  # postgres may still be warming up
        try:
            db.init_schema()
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("postgres not ready (%s) - retry %d/30", exc, attempt + 1)
            time.sleep(2)
    else:
        raise RuntimeError("postgres never became ready")
    seed.run()
    log.info("gateway ready | mock_mode=%s low_cost=%s auth=%s", settings.mock_mode, settings.low_cost_mode,
             settings.auth_required)
    yield


app = FastAPI(title="Drishti Cloud Gateway", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- auth
def _tenant(tenant_id: str) -> dict:
    hit = _tenant_cache.get(tenant_id)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    t = db.get_tenant(tenant_id)
    if not t:
        raise HTTPException(404, f"unknown tenant '{tenant_id}'")
    _tenant_cache[tenant_id] = (time.time(), t)
    return t


def _is_admin(request: Request) -> bool:
    token = request.headers.get("x-admin-token", "")
    return bool(settings.admin_token) and hmac.compare_digest(token, settings.admin_token)


def require_admin(request: Request) -> None:
    if settings.auth_required and not _is_admin(request):
        raise HTTPException(401, "admin token required")


def require_tenant(tenant_id: str, request: Request) -> dict:
    t = _tenant(tenant_id)
    if settings.auth_required and not _is_admin(request):
        key = request.headers.get("x-tenant-key", "")
        if not key or not hmac.compare_digest(key, t["api_key"]):
            raise HTTPException(401, "tenant key or admin token required")
    return t


def _public(t: dict) -> dict:
    return {k: v for k, v in t.items() if k != "api_key"}


# --------------------------------------------------------------------------- health
@app.get("/health")
def health():
    redis_ok = True
    try:
        bus.redis_client().ping()
    except Exception:  # noqa: BLE001
        redis_ok = False
    pg, n4j = db.ping(), graph.ping()
    return {
        "status": "ok" if (redis_ok and pg) else "degraded",
        "redis": redis_ok, "postgres": pg, "neo4j": n4j,
        "mock_mode": settings.mock_mode, "low_cost_mode": settings.low_cost_mode,
    }


@app.get("/verticals")
def verticals():
    return [{"id": k, "label": v["label"], "tenant_type": v["tenant_type"], "sam_labels": v["sam_labels"]}
            for k, v in VERTICALS.items()]


# --------------------------------------------------------------------------- tenants
class TenantIn(BaseModel):
    id: str
    vertical: str
    name: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=120)


@app.post("/tenants", status_code=201)
def create_tenant(body: TenantIn, request: Request):
    require_admin(request)
    if not TENANT_RE.match(body.id):
        raise HTTPException(422, "id must match ^[a-z0-9][a-z0-9_-]{1,62}$")
    if body.vertical not in VERTICALS:
        raise HTTPException(422, f"vertical must be one of {sorted(VERTICALS)}")
    try:
        t = db.create_tenant(body.id, body.vertical, body.name or body.id, body.location or "")
    except Exception as exc:  # noqa: BLE001
        if "duplicate key" in str(exc):
            raise HTTPException(409, f"tenant '{body.id}' already exists") from exc
        raise
    seed.register_in_graph(t)
    return {**t, "created_at": t["created_at"].isoformat()}


@app.get("/tenants")
def list_tenants(request: Request):
    require_admin(request)
    return db.tenants_with_stats()


@app.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: str, request: Request):
    return _public(require_tenant(tenant_id, request))


# --------------------------------------------------------------------------- ingest
def dhash(img: Image.Image, size: int = 16) -> int:
    g = img.convert("L").resize((size + 1, size), Image.BILINEAR)
    px = g.tobytes()  # mode "L": one byte per pixel
    bits = 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (px[row * (size + 1) + col] > px[row * (size + 1) + col + 1])
    return bits


@app.post("/ingest/{tenant_id}")
def ingest(
    tenant_id: str,
    request: Request,
    file: UploadFile = File(...),
    sensors: str = Form("{}"),
    camera_id: str = Form("cam_1"),
    force: bool = Form(False),
):
    t = require_tenant(tenant_id, request)
    r = bus.redis_client()

    if not bus.allow(f"rl:{tenant_id}", settings.max_frames_per_min, 60, r):
        raise HTTPException(429, f"rate limit: {settings.max_frames_per_min} frames/min per tenant")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", camera_id):
        raise HTTPException(422, "camera_id must be 1-40 chars of [A-Za-z0-9_-]")
    if len(sensors.encode()) > MAX_SENSORS_BYTES:
        raise HTTPException(413, "sensors JSON too large")
    try:
        sensor_data = json.loads(sensors or "{}")
        assert isinstance(sensor_data, dict)
    except (json.JSONDecodeError, AssertionError):
        raise HTTPException(422, "sensors must be a JSON object") from None
    if not settings.mock_mode:
        sensor_data.pop("_sim", None)  # simulator hints are only honoured offline

    raw = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"image larger than {settings.max_upload_mb} MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(415, "not a valid image") from None
    img = img.convert("RGB")
    img.thumbnail((640, 480))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    jpeg = buf.getvalue()

    bus.put_latest(tenant_id, camera_id, jpeg)   # dashboard live view, independent of dedupe / analysis

    # --- LOW_COST_MODE: static scene -> reuse cached segmentation / skip model calls
    h = dhash(img)
    hkey = f"scenehash:{tenant_id}:{camera_id}"
    prev = r.hgetall(hkey)
    now = time.time()
    pir = sensor_data.get("pir")
    static = False
    if prev and not force:
        dist = bin(h ^ int(prev["hash"])).count("1")
        fresh = now - float(prev["analyzed_ts"]) < settings.force_refresh_sec
        static = settings.low_cost_mode and dist <= settings.hash_distance and pir is not True and fresh
    if not static:
        r.hset(hkey, mapping={"hash": str(h), "analyzed_ts": str(now)})
        r.expire(hkey, 7 * 86400)

    frame_id = bus.new_id()
    bus.put_frame(frame_id, jpeg)
    bus.publish("raw_frames", tenant_id, {
        "frame_id": frame_id, "tenant_id": tenant_id, "vertical": t["vertical"], "camera_id": camera_id,
        "ts": bus.utcnow_iso(), "sensors": sensor_data, "static": static, "scene_hash": f"{h:064x}",
    }, r)
    return {"status": "queued", "frame_id": frame_id, "static": static}


# --------------------------------------------------------------------------- read API
def _iso(rows: list[dict], *fields: str) -> list[dict]:
    for row in rows:
        for f in fields:
            if row.get(f) is not None:
                row[f] = row[f].isoformat()
    return rows


@app.get("/tenants/{tenant_id}/cameras")
def cameras(tenant_id: str, request: Request):
    require_tenant(tenant_id, request)
    return bus.list_cameras(tenant_id)


@app.get("/tenants/{tenant_id}/cameras/{camera_id}/latest")
def camera_latest(tenant_id: str, camera_id: str, request: Request):
    require_tenant(tenant_id, request)
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", camera_id):
        raise HTTPException(422, "bad camera_id")
    img = bus.get_latest(tenant_id, camera_id)
    if img is None:
        raise HTTPException(404, "no frame received from this camera yet")
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/tenants/{tenant_id}/alerts")
def tenant_alerts(tenant_id: str, request: Request, limit: int = 50, vertical: str | None = None):
    require_tenant(tenant_id, request)
    rows = _iso(db.list_alerts(tenant_id, max(1, min(limit, 200))), "created_at")
    return [r for r in rows if not vertical or r["vertical"] == vertical]


@app.get("/tenants/{tenant_id}/events")
def tenant_events(tenant_id: str, request: Request, limit: int = 50):
    require_tenant(tenant_id, request)
    return _iso(db.list_events(tenant_id, max(1, min(limit, 200))), "created_at")


@app.get("/tenants/{tenant_id}/events/{frame_id}/image")
def event_image(tenant_id: str, frame_id: str, request: Request):
    """Picture behind an event: the snapshot kept for EVENT_IMAGE_TTL_SEC, else the alert's stored proof."""
    require_tenant(tenant_id, request)
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", frame_id):
        raise HTTPException(422, "bad frame id")
    img = bus.get_snapshot(tenant_id, frame_id) or db.get_alert_image_by_frame(tenant_id, frame_id)
    if img is None:
        raise HTTPException(404, "no picture kept for this event (expired or never stored)")
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/tenants/{tenant_id}/actuations")
def tenant_actuations(tenant_id: str, request: Request, limit: int = 30):
    require_tenant(tenant_id, request)
    return _iso(db.list_actuations(tenant_id, max(1, min(limit, 100))), "created_at")


@app.get("/tenants/{tenant_id}/graph")
def tenant_graph(tenant_id: str, request: Request):
    require_tenant(tenant_id, request)
    return graph.subgraph(tenant_id)


@app.get("/verticals/{vertical}/stats")
def vertical_stats(vertical: str, request: Request):
    require_admin(request)
    if vertical not in VERTICALS:
        raise HTTPException(404, "unknown vertical")
    return db.vertical_stats(vertical)


def _alert_or_404(alert_id: str, request: Request) -> dict:
    try:
        a = db.get_alert(alert_id)
    except Exception:  # noqa: BLE001 - malformed uuid
        a = None
    if not a:
        raise HTTPException(404, "alert not found")
    require_tenant(a["tenant_id"], request)
    return a


@app.get("/alerts/{alert_id}/image")
def alert_image(alert_id: str, request: Request):
    _alert_or_404(alert_id, request)
    img = db.get_alert_image(alert_id)
    if not img:
        raise HTTPException(404, "no image for this alert")
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


class AckIn(BaseModel):
    false_alarm: bool = False


@app.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, request: Request, body: AckIn = Body(default_factory=AckIn)):
    a = _alert_or_404(alert_id, request)
    db.ack_alert(alert_id, a["tenant_id"], body.false_alarm)
    if body.false_alarm and a["level"] == "ALERT" and a.get("rule") and a["rule"] != "llm_assessment":
        # Feedback loop: a false alarm teaches the home a habit for that hour.
        hour = a["created_at"].astimezone().hour
        graph.add_habit(
            a["tenant_id"], None, f"Learned: '{a['rule']}' around {hour:02d}:00 was a false alarm",
            rule=a["rule"], start_hour=hour, end_hour=min(24, hour + 1), learned=True,
        )
    return {"ok": True}


@app.get("/edge/{tenant_id}/actuate")
def edge_actuate(tenant_id: str, request: Request, max_items: int = 10):
    """Pi polls this; returns (and removes) queued speak / oled / isd1820 commands."""
    require_tenant(tenant_id, request)
    r = bus.redis_client()
    cmds = []
    for _ in range(max(1, min(max_items, 50))):
        raw = r.lpop(f"actuate:{tenant_id}")
        if raw is None:
            break
        cmds.append(json.loads(raw))
    ids = [c["id"] for c in cmds if "id" in c]
    if ids:
        db.set_actuation_status(ids, tenant_id, "delivered")
    return {"commands": cmds}


@app.get("/usage")
def usage(request: Request):
    require_admin(request)
    r = bus.redis_client()
    today = date.today().isoformat()
    models: dict[str, dict] = {}
    for key in r.scan_iter(f"usage:{today}:*"):
        models[key.split(":", 2)[2]] = {k: int(v) for k, v in r.hgetall(key).items()}
    return {"date": today, "models": models}


# --------------------------------------------------------------------------- operator notes
@app.post("/actions")
def actions(request: Request, payload: dict = Body(...)):
    """Tiny records API used by the architecture page's notes widget."""
    require_admin(request)
    action, args = payload.get("action"), payload.get("args") or {}
    if args.get("collection") != "notes":
        return Response(json.dumps({"error": "unknown collection"}), 400, media_type="application/json")
    try:
        if action == "records.list":
            rows = _iso(db.notes_list(), "created_at")
            return {"data": {"records": rows}}
        if action == "records.create":
            value = args.get("value")
            if not isinstance(value, dict) or len(json.dumps(value)) > 4000:
                raise ValueError("value must be a small object")
            row = db.notes_create(str(value.get("tenant_id") or "general")[:63], value)
            return {"data": _iso([row], "created_at")[0]}
        if action == "records.delete":
            db.notes_delete(str(args.get("id")))
            return {"data": {"ok": True}}
    except Exception as exc:  # noqa: BLE001
        return Response(json.dumps({"error": str(exc)[:200]}), 400, media_type="application/json")
    return Response(json.dumps({"error": "unknown action"}), 400, media_type="application/json")
