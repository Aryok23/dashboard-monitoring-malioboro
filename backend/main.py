import asyncio
import contextlib
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
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

load_dotenv()

from .auth.auth_handler import get_current_user, login
from .database import db as database
from .inference.detector import get_counts, load_model
from .stream.camera_manager import (
    CameraManager,
    _latest_jpegs,
    _latest_jpegs_lock,
    evaluate_threshold_alerts,
)

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
# Alert image storage
# ---------------------------------------------------------------------------

_ALERTS_DIR = os.path.join(os.path.dirname(__file__), "storage", "alerts")
os.makedirs(_ALERTS_DIR, exist_ok=True)
_ALERT_RETENTION_DAYS = int(os.environ.get("ALERT_RETENTION_DAYS", "30"))
_ALERT_CLEANUP_INTERVAL_SECS = 6 * 3600

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
    h, w = frame.shape[:2] if frame is not None else (720, 1280)

    payload = {
        "type": "detection",
        "camera_id": camera_id,
        "detections": detections,
        "counts": counts,
        "frame_width": w,
        "frame_height": h,
        "timestamp": datetime.utcnow().isoformat(),
    }

    for q in list(_ws_queues):
        try:
            _loop.call_soon_threadsafe(q.put_nowait, payload)
        except (asyncio.QueueFull, Exception):
            pass

    _detection_buffer.setdefault(camera_id, []).append(counts)

    camera = _CAMERA_MAP.get(camera_id, {})
    for alert_type, description in evaluate_threshold_alerts(camera, counts):
        _maybe_save_alert(
            camera_id, alert_type, description,
            frame=frame, detections=detections, trigger_value=_trigger_value(alert_type, counts),
        )


def _on_background_detection(camera_id: int, detections: list) -> None:
    counts = get_counts(detections)
    _detection_buffer.setdefault(camera_id, []).append(counts)


def _on_background_alert(
    camera_id: int, alert_type: str, description: str, frame, detections: list, counts: dict
) -> None:
    camera_name = _CAMERA_MAP.get(camera_id, {}).get("name", f"Camera {camera_id}")
    _maybe_save_alert(
        camera_id, alert_type, description or f"Background alert from {camera_name}",
        frame=frame, detections=detections, trigger_value=_trigger_value(alert_type, counts),
    )


def _trigger_value(alert_type: str, counts: dict) -> int:
    """HIGH_CROWD -> pedestrian count; HIGH_TRAFFIC -> all non-pedestrian
    classes summed. `counts` keys are the Indonesian class names from
    detector.get_counts() — "orang" is people."""
    if alert_type == "HIGH_CROWD":
        return counts.get("orang", 0)
    return sum(v for k, v in counts.items() if k != "orang")


def _save_alert_image(camera_id: int, frame) -> Optional[str]:
    """Encode the raw (unannotated) frame as JPEG and write it to
    _ALERTS_DIR. Bboxes are drawn client-side from the `detections` JSON at
    render time, so only one image file is kept per alert. Returns just the
    filename (no directory component) for storage in Alert.image_path."""
    if frame is None:
        return None
    import cv2

    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    filename = f"cam{camera_id}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}.jpg"
    with open(os.path.join(_ALERTS_DIR, filename), "wb") as f:
        f.write(buf.tobytes())
    return filename


def _maybe_save_alert(
    camera_id: int,
    alert_type: str,
    description: str,
    frame=None,
    detections: Optional[list] = None,
    trigger_value: int = 0,
) -> None:
    key = (camera_id, alert_type)
    now = datetime.utcnow()
    last = _last_alert.get(key)
    if last and (now - last).total_seconds() < _ALERT_COOLDOWN_SECS:
        return
    _last_alert[key] = now
    image_path = _save_alert_image(camera_id, frame)
    asyncio.run_coroutine_threadsafe(
        database.save_alert(
            camera_id, alert_type, description,
            trigger_value=trigger_value, image_path=image_path, detections=detections,
        ),
        _loop,
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
            avg = {k: round(sum(d.get(k, 0) for d in buf) / len(buf)) for k in keys}
            peak = max(sum(d.values()) for d in buf)
            # Buffer dicts are keyed by the Indonesian class names from
            # detector.get_counts() (TARGET_CLASSES) — "orang" is people.
            people_peak = max(d.get("orang", 0) for d in buf)
            await database.save_detection_log(
                camera_id, avg, peak_count=peak, people_peak=people_peak
            )
            _detection_buffer[camera_id] = []


async def _periodic_alert_cleanup() -> None:
    """Bounds backend/storage/alerts/ growth: delete alert rows (and their
    image files) older than _ALERT_RETENTION_DAYS. Runs a few times a day —
    retention is measured in days, no need to poll faster."""
    while True:
        await asyncio.sleep(_ALERT_CLEANUP_INTERVAL_SECS)
        try:
            deleted_images = await database.cleanup_old_alerts(_ALERT_RETENTION_DAYS)
            for filename in deleted_images:
                path = os.path.join(_ALERTS_DIR, os.path.basename(filename))
                if os.path.isfile(path):
                    os.remove(path)
            if deleted_images:
                logger.info("Alert cleanup: removed %d old alert(s).", len(deleted_images))
        except Exception:
            logger.exception("Alert cleanup failed")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _camera_manager, _loop

    _loop = asyncio.get_event_loop()
    await database.init_db()

    yolo_model = os.getenv("YOLO_MODEL", "yolo11lbest_finetuned.pt")
    load_model(yolo_model)

    _camera_manager = CameraManager(
        CAMERAS,
        _on_active_frame,
        _on_background_detection,
        _on_background_alert,
    )
    _camera_manager.start(default_camera_id=1)
    _log_task = asyncio.create_task(_periodic_log_writer())
    _cleanup_task = asyncio.create_task(_periodic_alert_cleanup())

    yield

    _log_task.cancel()
    _cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _log_task
    with contextlib.suppress(asyncio.CancelledError):
        await _cleanup_task

    _camera_manager.stop()

    # Skip Python's OpenCV/FFmpeg finalizers — they block for 30 s waiting for
    # cap.read() daemon threads to time out.  All real cleanup (DB writes,
    # camera stop) is already done above, so a hard exit is safe here.
    logging.shutdown()
    os._exit(0)


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
# MJPEG stream — no auth required (img tag fetches directly)
# ---------------------------------------------------------------------------


@app.get("/stream/mjpeg/{camera_id}")
async def stream_mjpeg(camera_id: int):
    if camera_id not in _CAMERA_MAP:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")

    async def generator():
        while True:
            with _latest_jpegs_lock:
                frame_bytes = _latest_jpegs.get(camera_id)
            if frame_bytes:
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + frame_bytes
                    + b"\r\n"
                )
            await asyncio.sleep(0.1)

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"},
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
    # One persistent receive task — completes when uvicorn sends a disconnect
    # during shutdown (connection.shutdown() puts a disconnect msg in the ASGI
    # receive queue).  Without this, the handler never sees the signal and
    # uvicorn blocks forever at "Waiting for connections to close."
    recv_task = asyncio.create_task(websocket.receive())
    get_task: asyncio.Task | None = None
    try:
        while True:
            get_task = asyncio.create_task(q.get())
            done, _ = await asyncio.wait(
                {get_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if recv_task in done:
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await get_task
                break
            payload = get_task.result()
            if payload is None:  # shutdown sentinel
                break
            try:
                await websocket.send_text(json.dumps(payload))
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket error: %s", exc)
    finally:
        if get_task is not None and not get_task.done():
            get_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await get_task
        recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await recv_task
        if q in _ws_queues:
            _ws_queues.remove(q)


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------


@app.get("/detections/history")
async def detections_history(camera_id: int, date: str, current_user: dict = Depends(get_current_user)):
    return await database.get_history(camera_id, date)


@app.get("/detections/summary")
async def detections_summary(camera_id: int, days: int = 7, current_user: dict = Depends(get_current_user)):
    """Per-WIB-calendar-day avg/peak total_count for the last `days` days."""
    return await database.get_weekly_trend(camera_id, days)


@app.get("/detections/hourly")
async def detections_hourly(camera_id: int, date: str, current_user: dict = Depends(get_current_user)):
    """Per-WIB-hour avg/peak total_count for one camera on one WIB calendar date."""
    return await database.get_hourly_trend(camera_id, date)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@app.get("/alerts")
async def list_alerts(
    camera_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unread_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """Defaults (unread_only=True, no filters) match the original
    notification-bell behaviour exactly. The "Riwayat Peringatan Keramaian"
    history page calls this with unread_only=false plus camera_id/date
    filters and offset-based pagination."""
    alerts = await database.get_alerts(
        camera_id=camera_id,
        start_date=start_date,
        end_date=end_date,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    for alert in alerts:
        camera = _CAMERA_MAP.get(alert["camera_id"], {})
        alert["camera_name"] = camera.get("name", f"Camera {alert['camera_id']}")
    return alerts


@app.get("/alerts/{alert_id}")
async def get_alert_detail(alert_id: int, current_user: dict = Depends(get_current_user)):
    alert = await database.get_alert_by_id(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    camera = _CAMERA_MAP.get(alert["camera_id"], {})
    alert["camera_name"] = camera.get("name", f"Camera {alert['camera_id']}")
    return alert


@app.get("/alerts/{alert_id}/image")
async def get_alert_image(alert_id: int, current_user: dict = Depends(get_current_user)):
    alert = await database.get_alert_by_id(alert_id)
    if not alert or not alert["image_path"]:
        raise HTTPException(status_code=404, detail="Image not found")
    # basename() strips any path components, so a tampered image_path can
    # only ever resolve to a file directly inside _ALERTS_DIR.
    filename = os.path.basename(alert["image_path"])
    full_path = os.path.join(_ALERTS_DIR, filename)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Image file not found")
    return FileResponse(full_path, media_type="image/jpeg")


@app.post("/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: int, current_user: dict = Depends(get_current_user)):
    await database.mark_alert_read(alert_id)
    return {"status": "ok"}
