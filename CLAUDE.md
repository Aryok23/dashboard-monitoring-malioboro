# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Real-time visitor and traffic density monitoring system for the Malioboro area, Yogyakarta, Indonesia. Undergraduate thesis (skripsi) project. Ingests live HLS streams from 22 CCTV cameras, runs YOLOv8 object detection, and displays results in a React dashboard.

## Commands

### Backend
```bash
# First time setup
cd malioboro-monitor
python -m venv venv
venv\Scripts\activate
pip install -r backend/requirements.txt   # includes bcrypt==4.0.1 — do not upgrade bcrypt

# Run (--timeout-graceful-shutdown required for clean Ctrl+C exit)
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 5

# Reset database (re-seeds admin from .env)
del backend\data\malioboro.db
```

### Frontend
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build
```

## Architecture

### Video pipeline
The browser plays HLS streams **directly** via `hls.js` — video never passes through Python. The backend serves an HLS proxy at `/proxy/hls/{camera_id}/master.m3u8` that rewrites playlist URLs to avoid CCTV server CORS issues. The WebSocket `/ws/active` carries only detection results (counts, bboxes, frame dims) — no frame data.

### Detection pipeline
`CameraManager` runs two components in background threads:
- `_ActiveCameraProcessor` — reads the selected camera at 3 FPS, runs YOLOv8 inference, pushes results to WebSocket queues via `loop.call_soon_threadsafe`
- `_BackgroundPollingPool` — cycles through all other cameras one at a time, grabs 1 frame per ~60s per camera, runs inference for alert checking

### Frontend bbox overlay
`LiveFeed.jsx` stacks a `<canvas>` over the `<video>` element. On each WebSocket detection message, `drawDetections()` scales bbox coordinates from the original OpenCV frame dimensions (`frame_width`/`frame_height` in the WS payload) to the canvas display size, accounting for `object-fit: contain` letterboxing.

### Database
SQLite at `backend/data/malioboro.db`. Created automatically on startup. Admin user seeded from `.env` only when the users table is empty — delete the `.db` file to re-seed with new credentials. Switch to PostgreSQL for deployment by changing the URL in `db.py` and adding `asyncpg`.

### Detection classes
9 target classes: `people`, `bicycle`, `motorcycle`, `bajaj`, `becak`, `andong`, `car`, `bus`, `truck`. COCO pretrained YOLOv8 covers 6 of these — bajaj, becak, andong require a fine-tuned model. Default model: `yolov8n.pt` (auto-downloaded). Override via `YOLO_MODEL=yolov8s.pt` in `.env`.

### Camera list
22 cameras in `backend/config/cameras.json`. Cameras 1–18 use `/malioboro/` base path; cameras 19–21 (Nol KM) use `/malioboro/` but camera 22 (`ATCS_kmnol`) uses `/atcs/`. If a Nol KM stream fails, verify the base path.

## Key files

| File | Role |
|------|------|
| `backend/main.py` | FastAPI app, HLS proxy, WebSocket broadcast, alert logic |
| `backend/stream/hls_reader.py` | Reads HLS stream, resolves master→chunklist, exponential backoff reconnect |
| `backend/stream/camera_manager.py` | Active processor + background polling pool |
| `backend/inference/detector.py` | YOLOv8 wrapper + COCO→our-class mapping |
| `backend/database/db.py` | Async DB helpers (init, seed, all query functions) |
| `frontend/src/pages/Dashboard.jsx` | WebSocket lifecycle, state owner for detections/frameSize/cameras |
| `frontend/src/components/LiveFeed.jsx` | hls.js player + canvas bbox overlay |
| `backend/config/cameras.json` | Camera registry (id, name, zone, master_url, is_ptz) |

## Important notes

- **bcrypt must stay at 4.0.1** — passlib was removed; auth uses `bcrypt` directly. bcrypt 4.0.0+ rejects passwords >72 bytes, which broke passlib's internal test vector
- **`__init__.py` files are required** in all backend subpackages — relative imports depend on them
- The `_on_active_frame` callback is called from a background thread — always use `loop.call_soon_threadsafe` or `asyncio.run_coroutine_threadsafe` to interact with the event loop from it
- Alert cooldown is 5 minutes per (camera_id, alert_type) pair to prevent spam
- Detection logs are written to DB every 5 minutes (aggregated average), not per-frame
