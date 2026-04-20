# Malioboro Monitor

**Sistem Pemantauan Kepadatan Pengunjung dan Lalu Lintas Secara Real-Time Menggunakan Computer Vision di Kawasan Malioboro, Yogyakarta**

Thesis project for undergraduate degree. The system ingests live HLS streams from 22 CCTV cameras along the Malioboro corridor, runs object detection (YOLOv8), and displays counts, charts, and alerts in a real-time web dashboard.

---

## Project Overview

| Component | Stack |
|-----------|-------|
| Backend   | FastAPI + SQLAlchemy (async) + SQLite + OpenCV |
| Frontend  | React 18 + Vite + Tailwind CSS + Recharts |
| Scraper   | Python (Google Colab) + OpenCV + Google Drive |
| Detection | YOLOv8 via Ultralytics (stub until weights are provided) |

Detection targets: `people`, `bicycle`, `motorcycle`, `bajaj`, `becak`, `andong`, `car`, `bus`, `truck`

---

## Backend Setup (Windows)

```bash
cd malioboro-monitor
python -m venv venv
venv\Scripts\activate
pip install -r backend/requirements.txt
copy .env.example .env
# Edit .env — set ADMIN_PASSWORD and JWT_SECRET at minimum
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

The backend will:
1. Create `backend/data/malioboro.db` and all tables automatically
2. Seed the admin user from `.env` if the users table is empty
3. Start streaming camera id=1 immediately using the stub detector

---

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Dashboard available at `http://localhost:5173`

---

## Scraper Setup (Google Colab)

1. Upload `scraper/frame_scraper.ipynb` to Google Colab
2. Run cells in order
3. Leave the final cell running — it collects frames continuously

Frames are saved to:
```
/content/drive/MyDrive/malioboro_dataset/raw_frames/{camera_id}/{date}/{time_slot}/frame_{timestamp}.jpg
```

Log CSV: `/content/drive/MyDrive/malioboro_dataset/scrape_log.csv`

---

## API Documentation

After starting the backend, visit `http://localhost:8000/docs` for the interactive Swagger UI.

---

## Loading the Real YOLO Model

Once you have trained weights (e.g., `best.pt`), edit `backend/inference/detector.py`:

1. Uncomment the `load_model` implementation
2. Uncomment the real inference path in `detect()`
3. Call `load_model("path/to/best.pt")` in `backend/main.py` during lifespan startup

---

## Known Issues / Things to Verify

- **Nol KM camera URLs** — Cameras 19–22 (`NolKm_GdAgung`, `NolKm_Utara`, `NolKm_Timur`, `ATCS_kmnol`) use the `/atcs/` base path in this project. If streams fail, try changing the base URL to `/malioboro/` in `backend/config/cameras.json` and `scraper/frame_scraper.py`.
- **Camera 1 `.stream` suffix** — Camera 1's stream key includes `.stream` as part of the path segment. The URL is already set correctly in `cameras.json`.
- **FFMPEG / OpenCV on Windows** — Ensure your Python environment has a working `opencv-python` build with FFMPEG support. Run `python -c "import cv2; print(cv2.getBuildInformation())"` and check the FFMPEG line shows `YES`.
- **Stub detector** — The system ships with a random-data stub. The frontend and database work fully with stub data, but detection counts are not real until YOLO weights are loaded.
