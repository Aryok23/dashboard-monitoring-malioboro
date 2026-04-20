import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

load_dotenv()

from .auth.auth_handler import get_current_user, login
from .database import db as database
from .inference.detector import get_counts
from .stream.camera_manager import CameraManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load camera list
# ---------------------------------------------------------------------------

_CAMERAS_FILE = os.path.join(os.path.dirname(__file__), "config", "cameras.json")
with open(_CAMERAS_FILE, encoding="utf-8") as _f:
    CAMERAS: list[dict] = json.load(_f)
_CAMERA_MAP: dict[int, dict] = {c["id"]: c for c in CAMERAS}

_HLS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_camera_manager: Optional[CameraManager] = None
_ws_queues: list[asyncio.Queue] = []
_detection_buffer: dict[int, list[dict]] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None
_last_alert: dict[tuple, datetime] = {}
_ALERT_COOLDOWN_SECS = 300


# ---------------------------------------------------------------------------
# Callbacks (called from background threads)
# ---------------------------------------------------------------------------


def _on_active_frame(camera_id: int, frame, detections: list) -> None:
    counts = get_counts(detections)

    payload = {
        "type": "detection",
        "camera_id": camera_id,
        "detections": detections,
        "counts": counts,
        "timestamp": datetime.utcnow().isoformat(),
    }

    for q in list(_ws_queues):
        try:
            _loop.call_soon_threadsafe(q.put_nowait, payload)
        except (asyncio.QueueFull, Exception):
            pass

    _detection_buffer.setdefault(camera_id, []).append(counts)

    p_thresh = int(os.getenv("ALERT_PEOPLE_THRESHOLD", "50"))
    v_thresh = int(os.getenv("ALERT_VEHICLE_THRESHOLD", "30"))
    people = counts.get("people", 0)
    vehicles = sum(
        counts.get(k, 0)
        for k in ("motorcycle", "car", "bus", "truck", "bajaj", "becak", "andong", "bicycle")
    )
    if people > p_thresh:
        _maybe_save_alert(camera_id, "HIGH_CROWD", f"People count {people} exceeded threshold {p_thresh}")
    if vehicles > v_thresh:
        _maybe_save_alert(camera_id, "HIGH_TRAFFIC", f"Vehicle count {vehicles} exceeded threshold {v_thresh}")


def _on_background_detection(camera_id: int, detections: list) -> None:
    counts = get_counts(detections)
    _detection_buffer.setdefault(camera_id, []).append(counts)


def _on_background_alert(camera_id: int, alert_type: str) -> None:
    camera_name = _CAMERA_MAP.get(camera_id, {}).get("name", f"Camera {camera_id}")
    _maybe_save_alert(camera_id, alert_type, f"Background alert from {camera_name}")


def _maybe_save_alert(camera_id: int, alert_type: str, description: str) -> None:
    key = (camera_id, alert_type)
    now = datetime.utcnow()
    last = _last_alert.get(key)
    if last and (now - last).total_seconds() < _ALERT_COOLDOWN_SECS:
        return
    _last_alert[key] = now
    asyncio.run_coroutine_threadsafe(
        database.save_alert(camera_id, alert_type, description), _loop
    )


# ---------------------------------------------------------------------------
# Periodic DB writer
# ---------------------------------------------------------------------------


async def _periodic_log_writer() -> None:
    while True:
        await asyncio.sleep(300)
        for camera_id, buf in list(_detection_buffer.items()):
            if not buf:
                continue
            keys = buf[0].keys()
            avg = {k: sum(d.get(k, 0) for d in buf) // len(buf) for k in keys}
            await database.save_detection_log(camera_id, avg)
            _detection_buffer[camera_id] = []


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _camera_manager, _loop

    _loop = asyncio.get_event_loop()
    await database.init_db()

    p_thresh = int(os.getenv("ALERT_PEOPLE_THRESHOLD", "50"))
    v_thresh = int(os.getenv("ALERT_VEHICLE_THRESHOLD", "30"))

    _camera_manager = CameraManager(
        CAMERAS,
        _on_active_frame,
        _on_background_detection,
        _on_background_alert,
        people_threshold=p_thresh,
        vehicle_threshold=v_thresh,
    )
    _camera_manager.start(default_camera_id=1)
    asyncio.create_task(_periodic_log_writer())

    yield

    _camera_manager.stop()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Malioboro Monitor API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# HLS Proxy — no auth required (video player fetches directly)
# ---------------------------------------------------------------------------

def _rewrite_m3u8(content: str, camera_id: int) -> str:
    """Rewrite relative URLs inside an m3u8 playlist to go through our proxy."""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if stripped.startswith("http"):
                # Absolute URL: keep only the filename, proxy it
                filename = stripped.rsplit("/", 1)[-1]
                lines.append(f"/proxy/hls/{camera_id}/{filename}")
            else:
                lines.append(f"/proxy/hls/{camera_id}/{stripped}")
        else:
            lines.append(line)
    return "\n".join(lines)


@app.get("/proxy/hls/{camera_id}/master.m3u8")
async def proxy_master(camera_id: int):
    camera = _CAMERA_MAP.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(camera["master_url"], headers=_HLS_HEADERS, timeout=15, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Upstream error: {exc}")

    rewritten = _rewrite_m3u8(resp.text, camera_id)
    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"},
    )


@app.get("/proxy/hls/{camera_id}/{filename:path}")
async def proxy_hls_segment(camera_id: int, filename: str):
    camera = _CAMERA_MAP.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404)

    base_url = camera["master_url"].rsplit("/", 1)[0] + "/"
    target_url = base_url + filename

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(target_url, headers=_HLS_HEADERS, timeout=20, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Upstream error: {exc}")

    content_type = resp.headers.get("content-type", "application/octet-stream")

    # Rewrite m3u8 chunklists too (chunklist contains TS segment URLs)
    if filename.endswith(".m3u8") or "mpegurl" in content_type:
        rewritten = _rewrite_m3u8(resp.text, camera_id)
        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"},
        )

    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Access-Control-Allow-Origin": "*"},
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
async def auth_login(data: LoginRequest):
    return await login(data.username, data.password)


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------


@app.get("/cameras")
async def get_cameras(current_user: dict = Depends(get_current_user)):
    return CAMERAS


@app.post("/camera/switch/{camera_id}")
async def switch_camera(camera_id: int, current_user: dict = Depends(get_current_user)):
    if _camera_manager is None:
        raise HTTPException(status_code=503, detail="Camera manager not ready")
    if camera_id not in _CAMERA_MAP:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    _camera_manager.switch_active_camera(camera_id)
    return {"status": "switched", "camera_id": camera_id}


# ---------------------------------------------------------------------------
# WebSocket — detection results only, no frame data
# ---------------------------------------------------------------------------


@app.websocket("/ws/active")
async def ws_active(websocket: WebSocket):
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _ws_queues.append(q)
    try:
        while True:
            payload = await asyncio.wait_for(q.get(), timeout=60)
            await websocket.send_text(json.dumps(payload))
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as exc:
        logger.debug("WebSocket error: %s", exc)
    finally:
        if q in _ws_queues:
            _ws_queues.remove(q)


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------


@app.get("/detections/history")
async def detections_history(camera_id: int, date: str, current_user: dict = Depends(get_current_user)):
    return await database.get_history(camera_id, date)


@app.get("/detections/summary")
async def detections_summary(camera_id: int, range: int = 7, current_user: dict = Depends(get_current_user)):
    return await database.get_summary(camera_id, range)


@app.get("/detections/heatmap")
async def detections_heatmap(date: str, current_user: dict = Depends(get_current_user)):
    return await database.get_heatmap(date)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@app.get("/alerts")
async def get_alerts(limit: int = 50, current_user: dict = Depends(get_current_user)):
    alerts = await database.get_unread_alerts(limit)
    for alert in alerts:
        camera = _CAMERA_MAP.get(alert["camera_id"], {})
        alert["camera_name"] = camera.get("name", f"Camera {alert['camera_id']}")
    return alerts


@app.post("/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: int, current_user: dict = Depends(get_current_user)):
    await database.mark_alert_read(alert_id)
    return {"status": "ok"}
