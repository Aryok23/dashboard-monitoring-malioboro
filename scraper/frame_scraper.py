"""
Malioboro Frame Scraper
=======================
Stratified time-sampling scraper for all 22 CCTV cameras at Malioboro, Yogyakarta.
Designed to run indefinitely inside Google Colab with Drive storage.

Usage:
    scraper = MalioboroScraper(drive_base_path, timezone)
    scraper.run_forever()
"""

import csv
import os
import time
import traceback
from datetime import datetime
from urllib.parse import urljoin

import cv2
import pytz
import requests
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Camera registry (all 22 cameras — no dependency on cameras.json)
# ---------------------------------------------------------------------------

CAMERAS = [
    {"id": 1,  "name": "Selatan Teteg",                   "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_1_Selatan_Teteg.stream/master.m3u8",               "zone": "Selatan"},
    {"id": 2,  "name": "Depan Toko Subur",                "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_2_Depan_Toko_Subur/master.m3u8",                  "zone": "Tengah"},
    {"id": 3,  "name": "Depan Dispar",                    "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_3_Depan_Dispar/master.m3u8",                       "zone": "Tengah"},
    {"id": 4,  "name": "Depan DPRD",                      "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_4_Depan_DPRD/master.m3u8",                         "zone": "Tengah"},
    {"id": 5,  "name": "Perwakilan",                      "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_5_Perwakilan/master.m3u8",                         "zone": "Tengah"},
    {"id": 6,  "name": "Mall Utara",                      "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_6_Mall_Utara/master.m3u8",                         "zone": "Tengah"},
    {"id": 7,  "name": "Mall Selatan",                    "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_7_Mall_Selatan/master.m3u8",                       "zone": "Tengah"},
    {"id": 8,  "name": "Mutiara Lama",                    "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_8_Mutiara_Lama/master.m3u8",                       "zone": "Tengah"},
    {"id": 9,  "name": "Mutiara Baru",                    "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_9_Mutiara_Baru/master.m3u8",                       "zone": "Tengah"},
    {"id": 10, "name": "Terang Bulan",                    "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_11_Terang_Bulan/master.m3u8",                      "zone": "Tengah"},
    {"id": 11, "name": "Ramayana",                        "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_12_Ramayana/master.m3u8",                          "zone": "Tengah"},
    {"id": 12, "name": "Gunungmas",                       "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_13_Gunungmas/master.m3u8",                         "zone": "Tengah"},
    {"id": 13, "name": "Utara Inna Malioboro",            "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_21_Utara_Inna_Malioboro/master.m3u8",              "zone": "Utara"},
    {"id": 14, "name": "Simpang Pajeksan Suryatmajan",    "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_22_Simpang_Pajeksan_Suryatmajan/master.m3u8",      "zone": "Utara"},
    {"id": 15, "name": "Depan BPD Kepatihan",             "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_23_Depan_BPD_Kepatihan/master.m3u8",               "zone": "Utara"},
    {"id": 16, "name": "Simpang Reksobayan",              "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_24_Simpang_Reksobayan/master.m3u8",                 "zone": "Utara"},
    {"id": 17, "name": "Utara Mall",                      "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_25_Utara_Mall/master.m3u8",                        "zone": "Utara"},
    {"id": 18, "name": "Pasar Beringharjo",               "master_url": "https://cctvjss.jogjakota.go.id/malioboro/Malioboro_30_Pasar_Beringharjo/master.m3u8",                 "zone": "Utara"},
    {"id": 19, "name": "Nol KM - Gedung Agung",           "master_url": "https://cctvjss.jogjakota.go.id/atcs/NolKm_GdAgung/master.m3u8",                                       "zone": "Nol KM"},
    {"id": 20, "name": "Nol KM - Utara",                  "master_url": "https://cctvjss.jogjakota.go.id/atcs/NolKm_Utara/master.m3u8",                                         "zone": "Nol KM"},
    {"id": 21, "name": "Nol KM - Timur",                  "master_url": "https://cctvjss.jogjakota.go.id/atcs/NolKm_Timur/master.m3u8",                                         "zone": "Nol KM"},
    {"id": 22, "name": "Simpang KM Nol (PTZ)",            "master_url": "https://cctvjss.jogjakota.go.id/atcs/ATCS_kmnol/master.m3u8",                                          "zone": "Nol KM"},
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Stratified time slot definitions (WIB local time)
_TIME_SLOTS = [
    {"name": "morning",  "start": (7, 0),  "end": (9, 0),  "interval_sec": 120},
    {"name": "midday",   "start": (12, 0), "end": (14, 0), "interval_sec": 120},
    {"name": "evening",  "start": (17, 0), "end": (20, 0), "interval_sec": 120},
    {"name": "night",    "start": (20, 0), "end": (23, 0), "interval_sec": 120},
]
_OFFPEAK_INTERVAL = 900  # 15 minutes


def _current_slot(now_local: datetime) -> tuple[str, int]:
    """Return (slot_name, interval_seconds) for the given local time."""
    hm = (now_local.hour, now_local.minute)
    for slot in _TIME_SLOTS:
        if slot["start"] <= hm < slot["end"]:
            return slot["name"], slot["interval_sec"]
    return "offpeak", _OFFPEAK_INTERVAL


def _resolve_chunklist(master_url: str) -> str | None:
    """Fetch master.m3u8 and return absolute chunklist URL."""
    try:
        resp = requests.get(master_url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line if line.startswith("http") else urljoin(master_url, line)
    except Exception:
        pass
    return None


def _grab_one_frame(chunklist_url: str):
    """Open chunklist, read one frame, immediately release. Returns numpy array or None."""
    cap = cv2.VideoCapture(chunklist_url)
    frame = None
    try:
        ret, frame = cap.read()
        if not ret:
            frame = None
    except Exception:
        frame = None
    finally:
        cap.release()
    return frame


class MalioboroScraper:
    def __init__(self, drive_base_path: str, timezone: str = "Asia/Jakarta"):
        self.base_path = drive_base_path
        self.tz = pytz.timezone(timezone)
        self.log_path = os.path.join(drive_base_path, "scrape_log.csv")
        self._ensure_log()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        print("Malioboro Scraper started. Press the stop button in Colab to halt.")
        cycle = 0
        while True:
            now_local = datetime.now(self.tz)
            slot_name, interval_sec = _current_slot(now_local)

            print(f"\n[Cycle {cycle}] {now_local.strftime('%Y-%m-%d %H:%M:%S')} | Slot: {slot_name} | Interval: {interval_sec}s")

            success, fail = self._run_one_cycle(slot_name)
            next_time = datetime.now(self.tz)
            next_str = next_time.strftime("%H:%M:%S")
            print(f"  Summary: {success} OK / {fail} FAIL | Next cycle in {interval_sec}s (~{next_str})")

            time.sleep(interval_sec)
            cycle += 1

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_one_cycle(self, slot_name: str) -> tuple[int, int]:
        now_local = datetime.now(self.tz)
        date_str = now_local.strftime("%Y-%m-%d")
        success = 0
        fail = 0

        for camera in tqdm(CAMERAS, desc="Cameras", unit="cam", leave=False):
            cam_id = camera["id"]
            cam_name = camera["name"]
            ts = datetime.now(self.tz).strftime("%H%M%S")
            file_path = ""
            error_msg = ""
            status = "fail"

            try:
                chunklist_url = _resolve_chunklist(camera["master_url"])
                if chunklist_url is None:
                    raise RuntimeError("Could not resolve chunklist")

                frame = _grab_one_frame(chunklist_url)
                if frame is None:
                    raise RuntimeError("Empty frame returned")

                # Build output path
                out_dir = os.path.join(
                    self.base_path, "raw_frames",
                    str(cam_id), date_str, slot_name,
                )
                os.makedirs(out_dir, exist_ok=True)
                file_path = os.path.join(out_dir, f"frame_{ts}.jpg")

                # Write with retry
                saved = False
                for attempt in range(2):
                    try:
                        cv2.imwrite(file_path, frame)
                        saved = True
                        break
                    except Exception as write_err:
                        if attempt == 0:
                            time.sleep(1)
                        else:
                            raise write_err

                if saved:
                    status = "ok"
                    success += 1

            except Exception as exc:
                error_msg = str(exc)
                fail += 1

            self._log_row(cam_id, cam_name, datetime.now(self.tz).isoformat(), slot_name, status, file_path, error_msg)

        return success, fail

    def _ensure_log(self) -> None:
        os.makedirs(self.base_path, exist_ok=True)
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["camera_id", "camera_name", "timestamp", "time_slot", "status", "file_path", "error_message"])

    def _log_row(self, camera_id, camera_name, timestamp, time_slot, status, file_path, error_message) -> None:
        try:
            with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([camera_id, camera_name, timestamp, time_slot, status, file_path, error_message])
        except Exception:
            pass  # Never let logging crash the scraper
