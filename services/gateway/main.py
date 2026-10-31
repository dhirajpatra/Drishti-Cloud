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
from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError
from pydantic import BaseModel, Field

from drishti import bus, db, graph, inventory, siteconfig
from drishti.config import settings
from drishti.log import get_logger
from drishti.verticals import VERTICALS, sop_for
import seed
from admin import build_router

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
app.include_router(build_router(require_admin, _tenant))


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


def image_quality(img: Image.Image) -> dict:
    """Cheap local look at the frame, like a guard noticing 'the picture went black / blurry': brightness, contrast, sharpness."""
    g = img.convert("L").resize((160, 120))
    st = ImageStat.Stat(g)
    sharp = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0]
    return {"brightness": round(st.mean[0], 1), "contrast": round(st.stddev[0], 1), "sharpness": round(sharp, 1)}


def is_blocked(q: dict) -> str | None:
    """Why the view looks lost (covered lens, blacked out, white-out), else None. A normal dim room still has texture/noise."""
    if q["contrast"] < 2.0:
        return "picture is a flat colour"
    if q["brightness"] < 8 and q["contrast"] < 6:
        return "picture is black"
    return None


def brighten(img: Image.Image) -> Image.Image:
    """Deterministic low-light help (contrast stretch + gamma), never generative: it cannot invent people or objects."""
    out = ImageOps.autocontrast(img, cutoff=1)
    return out.point([int(255 * ((v / 255) ** 0.6)) for v in range(256)] * 3)


PANEL = (320, 240)


def make_storyboard(panels: list[tuple[Image.Image, str]]) -> bytes:
    """Contact sheet, oldest panel first (left to right, top to bottom), each labelled with its time. One image = one model
    call, yet the model can see movement: standing -> lying (fall), arm raised -> contact (hit), someone entering."""
    from PIL import ImageDraw
    n = len(panels)
    cols = 1 if n == 1 else 2
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (PANEL[0] * cols, PANEL[1] * rows), (0, 0, 0))
    for i, (im, label) in enumerate(panels):
        tile = im.copy()
        tile.thumbnail(PANEL)
        x, y = (i % cols) * PANEL[0], (i // cols) * PANEL[1]
        sheet.paste(tile, (x + (PANEL[0] - tile.width) // 2, y + (PANEL[1] - tile.height) // 2))
        d = ImageDraw.Draw(sheet)
        d.rectangle([x, y, x + 8 + 7 * len(label), y + 16], fill=(0, 0, 0))
        d.text((x + 4, y + 2), label, fill=(255, 255, 255))
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=80)
    return out.getvalue()


def ago(sec: float) -> str:
    return f"{int(sec)}s ago" if sec < 90 else f"{int(sec // 60)} min ago"


GRID = (32, 24)


def scene_signature(img: Image.Image) -> list[int]:
    """32x24 area-averaged grey cells with the global brightness removed (lamps / auto-exposure shift every cell equally)."""
    cells = list(img.convert("L").resize(GRID, Image.BOX).tobytes())
    mean = sum(cells) / len(cells)
    return [round(c - mean) for c in cells]


def change_fraction(a: list[int], b: list[int], delta: int) -> float:
    """Share of cells that moved by more than `delta` grey levels between two signatures."""
    return sum(1 for x, y in zip(a, b) if abs(x - y) > delta) / len(a)


@app.post("/ingest/{tenant_id}")
def ingest(
    tenant_id: str,
    request: Request,
    file: UploadFile = File(...),
    sensors: str = Form("{}"),
    camera_id: str = Form("cam_1"),
    force: bool = Form(False),
    burst: list[UploadFile] = File(default=[]),     # earlier frames of the last few seconds, oldest first (motion hook)
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
    hires = img.copy()
    hires.thumbnail((1280, 960))
    img.thumbnail((640, 480))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    jpeg = buf.getvalue()

    bus.put_latest(tenant_id, camera_id, jpeg)   # dashboard live view (the true picture), independent of dedupe / analysis

    quality = image_quality(img)
    blocked = is_blocked(quality)
    skey = f"camstate:{tenant_id}:{camera_id}"
    was_blocked = r.get(skey) == "blocked"
    r.set(skey, "blocked" if blocked else "ok", ex=7 * 86400)
    just_blocked = bool(blocked) and not was_blocked                      # alert once when the view is lost, not every frame
    if was_blocked and not blocked:
        bus.notify(tenant_id, "camera", "INFO", f"Camera {camera_id} view is back", "The picture is normal again.", r=r)
    quality["obstructed"], quality["why"] = just_blocked, blocked
    model_img = img
    if not blocked and quality["brightness"] < 55:                        # dim scene: brighten what the models see
        model_img = brighten(img)
        quality["enhanced"] = True
        b2 = io.BytesIO()
        model_img.save(b2, "JPEG", quality=80)
        jpeg = b2.getvalue()

    # --- LOW_COST_MODE: idle scene -> reuse cached analysis, no model call
    h = dhash(img)
    sig = scene_signature(img)
    hkey = f"scenehash:{tenant_id}:{camera_id}"
    prev = r.hgetall(hkey)
    now = time.time()
    samp = siteconfig.effective(tenant_id, camera_id, t["vertical"])["sampling"]
    pir = sensor_data.get("pir")
    static, change = False, None
    if prev and not force and settings.low_cost_mode and pir is not True and not just_blocked:
        age = now - float(prev["analyzed_ts"])
        if prev.get("sig"):
            change = change_fraction(sig, json.loads(prev["sig"]), settings.cell_delta)
            changed = change > samp["change_fraction"]
        else:  # entry written by an older version: fall back to the perceptual hash
            changed = bin(h ^ int(prev["hash"])).count("1") > settings.hash_distance
        # idle while nothing moved; also hold a changed scene back until the minimum gap since the last analysis has passed
        # (the baseline stays put, so the change is picked up by the first sample after the gap)
        big_jump = change is not None and change >= settings.burst_fraction and age >= settings.burst_gap_sec   # fight / fall / panic
        static = age < settings.force_refresh_sec and (not changed or (age < samp["min_analysis_gap_sec"] and not big_jump))
    if not static:
        r.hset(hkey, mapping={"hash": str(h), "sig": json.dumps(sig), "analyzed_ts": str(now)})
        r.expire(hkey, 7 * 86400)

    frame_id = bus.new_id()
    story_note = None
    if not static:
        if hires.width > img.width:                                      # camera sends more pixels than the 640x480 the models get
            hb = io.BytesIO()
            hires.save(hb, "JPEG", quality=85)
            bus.put_hires(frame_id, hb.getvalue())
        panels: list[tuple[Image.Image, str]] = []
        if samp["storyboard"]:
            before, age = bus.get_baseline(tenant_id, camera_id)
            if before is not None and age is not None:
                panels.append((Image.open(io.BytesIO(before)), f"BEFORE {ago(age)}"))
            extra = []
            for f in burst[:8]:
                try:
                    im = Image.open(io.BytesIO(f.file.read(settings.max_upload_mb * 1024 * 1024))).convert("RGB")
                    im.thumbnail(PANEL)
                    extra.append(im)
                except (UnidentifiedImageError, OSError):
                    continue
            keep = extra if len(extra) <= 2 else [extra[0], extra[len(extra) // 2]]   # max 4 panels in the sheet
            for k, im in enumerate(keep):
                panels.append((im, f"t-{(len(keep) - k) * settings.burst_spacing_sec * (len(extra) / max(1, len(keep))):.0f}s"))
            if panels:
                panels.append((model_img, "NOW"))
                bus.put_story(frame_id, make_storyboard(panels))
                story_note = ("the image is a storyboard of the same camera, oldest panel first (left to right, top to bottom); "
                              "judge movement across panels (falling, hitting, entering, leaving) and decide about the NOW panel")
        small = model_img.copy()
        small.thumbnail(PANEL)
        sb = io.BytesIO()
        small.save(sb, "JPEG", quality=70)
        bus.put_baseline(tenant_id, camera_id, sb.getvalue())
    bus.put_frame(frame_id, jpeg)
    bus.publish("raw_frames", tenant_id, {
        "frame_id": frame_id, "tenant_id": tenant_id, "vertical": t["vertical"], "camera_id": camera_id,
        "ts": bus.utcnow_iso(), "sensors": sensor_data, "static": static, "scene_hash": f"{h:064x}",
        "quality": {k: v for k, v in quality.items() if v not in (None, False)},
        **({"storyboard": story_note} if story_note else {}),
    }, r)
    return {"status": "queued", "frame_id": frame_id, "static": static,
            "change": None if change is None else round(change, 3)}


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


FEED_URL = re.compile(r"^(https?|rtsps?)://[^\s]{4,500}$|^/app/feeds/[A-Za-z0-9_\-./]{1,200}$")


def _mask(url: str) -> str:
    return re.sub(r"//[^/@\s]+@", "//***@", url)


class FeedBody(BaseModel):
    camera_id: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=4, max_length=500)


@app.get("/tenants/{tenant_id}/feeds")
def list_feeds(tenant_id: str, request: Request):
    """Camera sources added from the dashboard (credentials in the URL are masked)."""
    require_tenant(tenant_id, request)
    return [{"camera_id": c["camera_id"], "url": _mask(siteconfig.build_url(c))} for c in db.list_camera_sources(tenant_id)]


@app.post("/tenants/{tenant_id}/feeds", status_code=201)
def add_feed(tenant_id: str, body: FeedBody, request: Request):
    """Register a camera: motionEye/ESP32/phone snapshot or MJPEG, RTSP, an HLS (.m3u8) live URL or an MP4 under ./feeds.
    The feed-ingestor picks it up on its next sample. Facebook/Instagram live pages are NOT accepted: Meta offers no API to read them."""
    t = require_tenant(tenant_id, request)
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", body.camera_id):
        raise HTTPException(422, "camera_id must be 1-40 chars of [A-Za-z0-9_-]")
    if ".." in body.url or not FEED_URL.match(body.url):
        raise HTTPException(422, "url must be http(s)://, rtsp(s):// or /app/feeds/<file>")
    if re.match(r"https?://([^/@]*@)?([^/:]*\.)?(facebook|fb|instagram)\.(com|watch)\b", body.url, re.I):
        raise HTTPException(422, "Facebook/Instagram pages cannot be read as a camera feed; use the camera's own RTSP/HTTP URL")
    parts = siteconfig.parse_url(body.url)
    try:
        siteconfig.save_source(tenant_id, body.camera_id, {**parts, "enabled": True})
    except siteconfig.ConfigError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"camera_id": body.camera_id, "url": _mask(body.url), "note": "picked up on the next sample"}


@app.delete("/tenants/{tenant_id}/feeds/{camera_id}")
def remove_feed(tenant_id: str, camera_id: str, request: Request):
    require_tenant(tenant_id, request)
    if not db.delete_camera_source(tenant_id, camera_id):
        raise HTTPException(404, "no such registered camera (cameras from RTSP_FEEDS are managed in .env)")
    return {"ok": True}


@app.api_route("/hook/motion/{tenant_id}/{camera_id}", methods=["GET", "POST"])
def motion_hook(tenant_id: str, camera_id: str, token: str = ""):
    """motionEye "Call a Web Hook" target: a motion event makes the feed-ingestor grab the camera's current frame at once and
    analyse it (PIR=true, so the idle gate is bypassed). At most one trigger per camera per MOTION_HOOK_COOLDOWN_SEC (cost guard)."""
    _tenant(tenant_id)
    want = settings.motion_hook_token
    if not want:
        if settings.auth_required:
            raise HTTPException(403, "set MOTION_HOOK_TOKEN to use the motion web hook")
    elif not hmac.compare_digest(token, want):
        raise HTTPException(401, "bad token")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", camera_id):
        raise HTTPException(422, "bad camera_id")
    r = bus.redis_client()
    if not r.set(f"hookcd:{tenant_id}:{camera_id}", 1, nx=True, ex=max(1, settings.motion_hook_cooldown_sec)):
        return {"status": "cooldown", "retry_after_sec": r.ttl(f"hookcd:{tenant_id}:{camera_id}")}
    r.lpush("trigger:feed", json.dumps({"tenant": tenant_id, "camera": camera_id, "ts": bus.utcnow_iso()}))
    r.ltrim("trigger:feed", 0, 99)
    return {"status": "triggered"}


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


@app.get("/tenants/{tenant_id}/notifications")
def tenant_notifications(tenant_id: str, request: Request, limit: int = 50):
    require_tenant(tenant_id, request)
    rows = bus.notifications(tenant_id, max(1, min(limit, 100)))
    return {"unread": sum(1 for n in rows if not n["read"]), "items": rows}


@app.post("/tenants/{tenant_id}/notifications/read")
def tenant_notifications_read(tenant_id: str, request: Request, body: dict = Body(default={})):
    require_tenant(tenant_id, request)
    ids = body.get("ids")
    if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
        raise HTTPException(422, "ids must be a list of strings (omit it to mark everything read)")
    return {"changed": bus.notifications_read(tenant_id, ids)}


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


# --------------------------------------------------------------------------- stock (fridge / medicine / shelf)
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
    if body.camera_id and not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", body.camera_id):
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
