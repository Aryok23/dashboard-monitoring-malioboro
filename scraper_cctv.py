"""
scraper_cctv.py — Malioboro Monitor Dataset Scraper (Production)
=================================================================
Grabs one frame per camera per scheduled time slot, with built-in
scheduler so you DON'T need cron or Task Scheduler.

Just run ONCE and leave it running for the whole week:
    python scraper_cctv.py

It scrapes automatically at every slot, then sleeps until the next one.
Stop anytime with Ctrl+C — already-saved frames are kept.

Manual single run (testing or missed slots):
    python scraper_cctv.py --now
    python scraper_cctv.py --now --slot noon
    python scraper_cctv.py --now --cameras 1 3 7

1-WEEK SCHEDULE (8 slots/day × 22 cameras × 7 days = 1,232 frames target):
    06:00  early_morning    baseline, low crowd
    08:00  morning          commute peak
    10:00  midmorning       tourist arrival
    12:00  noon             lunch rush
    14:00  afternoon        hottest hour, still busy
    16:00  late_afternoon   pre-evening rush
    18:00  evening          peak bajaj/becak/andong activity
    20:00  night            night market

Output:
    dataset/raw_frames/   — JPEG frames
    dataset/metadata.csv  — one row per attempt (success or failed)
    dataset/logs/         — daily log files
"""

import argparse
import csv
import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import httpx
import numpy as np

try:
    import m3u8
    HAS_M3U8 = True
except ImportError:
    HAS_M3U8 = False

# ── Config ────────────────────────────────────────────────────────────────────

CAMERAS_JSON  = Path("backend/config/cameras.json")
OUTPUT_DIR    = Path("dataset/raw_frames")
METADATA_CSV  = Path("dataset/metadata.csv")
LOG_DIR       = Path("dataset/logs")

LAT, LON      = -7.7928, 110.3653
HLS_TIMEOUT_S = 15
JPEG_QUALITY  = 92

# 8 slots per day — covers all crowd conditions + lighting for thesis
DAILY_SCHEDULE = [
    (0, 0,  "midnight"),
    (2, 0,  "dawn"),
    (6,  0,  "early_morning"),
    (8,  0,  "morning"),
    (10, 0,  "midmorning"),
    (12, 0,  "noon"),
    (14, 0,  "afternoon"),
    (16, 0,  "late_afternoon"),
    (18, 0,  "evening"),
    (20, 0,  "night"),
    (22, 0,  "late_night"),
]

# Collection window: today → next Wednesday
_today = date.today()
_days_to_wed = (2 - _today.weekday()) % 7
COLLECTION_END = _today + timedelta(days=(_days_to_wed if _days_to_wed > 0 else 7))


# ── Logger ────────────────────────────────────────────────────────────────────

def setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"scraper_{datetime.now().strftime('%Y%m%d')}.log"
    lg = logging.getLogger("scraper")
    lg.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    ch.setLevel(logging.INFO)
    lg.addHandler(fh)
    lg.addHandler(ch)
    return lg

logger = setup_logger()


# ── Weather ───────────────────────────────────────────────────────────────────

def get_weather() -> dict:
    try:
        r = httpx.get(
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={LAT}&longitude={LON}"
            f"&current=temperature_2m,precipitation,weathercode,cloudcover"
            f"&timezone=Asia%2FJakarta",
            timeout=10,
        )
        r.raise_for_status()
        c    = r.json()["current"]
        code = c["weathercode"]
        if code == 0:                condition = "clear"
        elif code in (1, 2, 3):      condition = "cloudy"
        elif code in range(51, 68):  condition = "drizzle"
        elif code in range(80, 100): condition = "rain"
        else:                        condition = "other"
        return {"condition": condition, "temp_c": c["temperature_2m"],
                "rain_mm": c["precipitation"], "cloud_pct": c["cloudcover"],
                "wmo_code": code}
    except Exception as e:
        logger.warning(f"Weather API failed: {e}")
        return {"condition": "unknown", "temp_c": None, "rain_mm": None,
                "cloud_pct": None, "wmo_code": None}


# ── HLS Frame Grab ────────────────────────────────────────────────────────────

def resolve_chunklist(master_url: str) -> str | None:
    if not HAS_M3U8:
        return None
    try:
        r   = httpx.get(master_url, timeout=HLS_TIMEOUT_S, follow_redirects=True)
        pl  = m3u8.loads(r.text)
        if pl.playlists:
            uri = pl.playlists[0].uri
            return uri if uri.startswith("http") else f"{master_url.rsplit('/',1)[0]}/{uri}"
        return master_url
    except Exception as e:
        logger.debug(f"HLS resolve error: {e}")
        return None


def grab_frame(master_url: str, retry_limit: int = 3):
    """
    Grab a single frame from an HLS stream.
    Adopted from working Colab approach:
      - sleep 2s after open to let stream stabilize
      - flush 30 frames to get past the buffer (not just 3)
      - retry up to retry_limit times before giving up
    """
    urls = [master_url]
    chunk = resolve_chunklist(master_url)
    if chunk and chunk != master_url:
        urls.append(chunk)

    for url in urls:
        for attempt in range(retry_limit):
            cap = None
            try:
                logger.debug(f"    grab attempt {attempt+1}/{retry_limit} — {url}")
                cap = cv2.VideoCapture(url)
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, HLS_TIMEOUT_S * 1000)
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, HLS_TIMEOUT_S * 1000)

                if not cap.isOpened():
                    logger.debug(f"    cap not opened on attempt {attempt+1}")
                    time.sleep(2)
                    continue

                # Wait for stream to stabilize — critical for jogjakota CCTV streams
                time.sleep(2)

                # Flush buffer — 30 frames like the working Colab code
                for _ in range(30):
                    cap.read()

                ret, frame = cap.read()
                if ret and frame is not None:
                    return True, frame
                else:
                    logger.debug(f"    frame read failed on attempt {attempt+1}, retrying...")
                    time.sleep(2)

            except Exception as e:
                logger.debug(f"    cv2 error attempt {attempt+1}: {e}")
                time.sleep(2)
            finally:
                if cap:
                    cap.release()

    return False, None


# ── Quality Checks ────────────────────────────────────────────────────────────

def is_blurry(frame, threshold=80.0) -> bool:
    return cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() < threshold

def is_dark(frame, threshold=20.0) -> bool:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean() < threshold


# ── Metadata CSV ──────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "filename", "camera_id", "camera_name", "zone", "is_ptz",
    "timestamp", "date", "time_hhmm", "slot", "day_of_week",
    "weather_condition", "temp_c", "rain_mm", "cloud_pct", "wmo_code",
    "width", "height", "blurry", "dark", "status",
]

def init_csv():
    METADATA_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not METADATA_CSV.exists():
        with open(METADATA_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

def write_csv(row: dict):
    with open(METADATA_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


# ── Core Scrape Run ───────────────────────────────────────────────────────────

def run_scrape(slot: str, camera_ids: list | None = None):
    with open(CAMERAS_JSON) as f:
        cameras = json.load(f)
    if camera_ids:
        cameras = [c for c in cameras if c["id"] in camera_ids]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_csv()

    now      = datetime.now()
    ts_str   = now.strftime("%Y%m%d_%H%M%S")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    dow      = now.strftime("%A")

    logger.info(f"\n{'='*60}")
    logger.info(f"  SCRAPE | {dow} {date_str} {time_str} | slot={slot} | {len(cameras)} cams")
    logger.info(f"{'='*60}")

    weather = get_weather()
    logger.info(f"  Weather: {weather['condition']}, {weather['temp_c']}°C, "
                f"rain {weather['rain_mm']}mm\n")

    success, failed = 0, 0

    for cam in cameras:
        cam_id, cam_name = cam["id"], cam["name"]
        zone, is_ptz     = cam["zone"], cam.get("is_ptz", False)
        logger.info(f"  [{cam_id:02d}] {cam_name}")

        ok, frame = grab_frame(cam["master_url"])

        base = dict(
            camera_id=cam_id, camera_name=cam_name, zone=zone, is_ptz=is_ptz,
            timestamp=ts_str, date=date_str, time_hhmm=time_str,
            slot=slot, day_of_week=dow,
            weather_condition=weather["condition"], temp_c=weather["temp_c"],
            rain_mm=weather["rain_mm"], cloud_pct=weather["cloud_pct"],
            wmo_code=weather["wmo_code"],
        )

        if not ok:
            logger.warning(f"       ✗ Failed")
            write_csv({**base, "filename": "", "width": None, "height": None,
                       "blurry": None, "dark": None, "status": "failed"})
            failed += 1
            continue

        h, w      = frame.shape[:2]
        blurry    = is_blurry(frame)
        dark      = is_dark(frame)
        safe_name = re.sub(r"[^\w\-]", "_", cam_name)
        filename  = f"cam{cam_id:02d}_{safe_name}_{ts_str}_{slot}_{weather['condition']}.jpg"
        filepath  = OUTPUT_DIR / filename

        cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

        flags    = [f for f, v in [("BLURRY", blurry), ("DARK", dark)] if v]
        flag_str = ",".join(flags) or "ok"
        logger.info(f"       ✓ {w}×{h} | {flag_str} → {filename}")

        write_csv({**base, "filename": filename, "width": w, "height": h,
                   "blurry": blurry, "dark": dark, "status": "ok"})
        success += 1
        time.sleep(0.5)

    total = len(list(OUTPUT_DIR.glob("*.jpg")))
    logger.info(f"\n  Done: {success} saved, {failed} failed | Total so far: {total}\n")
    return success, failed


# ── Built-in Scheduler ────────────────────────────────────────────────────────

def run_scheduler(camera_ids=None):
    logger.info(f"\n{'='*60}")
    logger.info(f"  MALIOBORO SCRAPER — 1-WEEK AUTO SCHEDULER")
    logger.info(f"  Start : {date.today()}")
    logger.info(f"  End   : {COLLECTION_END} (Wednesday)")
    logger.info(f"  Slots : {len(DAILY_SCHEDULE)}/day × 22 cams × "
                f"{(COLLECTION_END - date.today()).days} days")
    logger.info(f"  Target: ~{len(DAILY_SCHEDULE) * 22 * (COLLECTION_END - date.today()).days} frames")
    logger.info(f"  Stop  : Ctrl+C (frames already saved are kept)")
    logger.info(f"{'='*60}\n")

    # Show today's remaining slots
    now = datetime.now()
    logger.info("  Today's remaining slots:")
    any_remaining = False
    for h, m, label in DAILY_SCHEDULE:
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot_dt > now:
            logger.info(f"    {h:02d}:{m:02d} — {label}")
            any_remaining = True
    if not any_remaining:
        logger.info("    (none left today — first run will be tomorrow 06:00)")
    logger.info("")

    try:
        while True:
            today = date.today()
            if today > COLLECTION_END:
                logger.info("Collection window complete. All done!")
                break

            # Find next slot
            now      = datetime.now()
            next_dt  = None
            next_slot = None
            for day_offset in range(2):
                d = today + timedelta(days=day_offset)
                if d > COLLECTION_END:
                    break
                for h, m, label in DAILY_SCHEDULE:
                    candidate = datetime(d.year, d.month, d.day, h, m, 0)
                    if candidate > now:
                        next_dt   = candidate
                        next_slot = label
                        break
                if next_dt:
                    break

            if not next_dt:
                logger.info("No more slots in collection window.")
                break

            wait_s = (next_dt - datetime.now()).total_seconds()
            wm, ws = divmod(int(wait_s), 60)
            logger.info(f"  ⏰ Next: {next_dt.strftime('%a %d %b %H:%M')} "
                        f"[{next_slot}] — sleeping {wm}m {ws}s ...")

            # Sleep in 30s chunks for Ctrl+C responsiveness
            deadline = time.time() + wait_s
            while time.time() < deadline - 1:
                time.sleep(min(30, deadline - time.time()))

            run_scrape(slot=next_slot, camera_ids=camera_ids)

    except KeyboardInterrupt:
        n = len(list(OUTPUT_DIR.glob("*.jpg"))) if OUTPUT_DIR.exists() else 0
        logger.info(f"\n  Stopped by user. {n} frames saved total.")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Malioboro CCTV scraper — run once, schedules itself for 1 week"
    )
    parser.add_argument("--now", action="store_true",
                        help="Run a single scrape immediately (skip scheduler)")
    parser.add_argument("--slot", type=str, default=None,
                        help="Slot label when using --now (default: auto)")
    parser.add_argument("--cameras", nargs="*", type=int,
                        help="Camera IDs to scrape (default: all 22)")
    args = parser.parse_args()

    if args.now:
        slot = args.slot
        if not slot:
            h = datetime.now().hour
            slot = next((lbl for hr, _, lbl in reversed(DAILY_SCHEDULE) if h >= hr), "manual")
        run_scrape(slot=slot, camera_ids=args.cameras)
    else:
        run_scheduler(camera_ids=args.cameras)
