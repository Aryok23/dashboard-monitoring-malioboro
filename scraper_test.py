"""
scraper_test.py — Quick Connection Test
========================================
Run this FIRST before setting up the full scraper.
Tests 3 things:
  1. Can we reach the Open-Meteo weather API?
  2. Can we resolve the HLS master playlist for each camera?
  3. Can we grab at least one frame?

Usage:
    python scraper_test.py                  # test all 22 cameras
    python scraper_test.py --cameras 1 2 3  # test specific cameras only
    python scraper_test.py --fast           # only check URL reachability, skip frame grab

Takes ~2-5 minutes for all 22 cameras.
"""

import argparse
import json
import time
from pathlib import Path
from datetime import datetime

import cv2
import httpx

try:
    import m3u8
    HAS_M3U8 = True
except ImportError:
    HAS_M3U8 = False

CAMERAS_JSON = Path("backend/config/cameras.json")
LAT, LON     = -7.7928, 110.3653
TIMEOUT_S    = 15

# ── Colors for terminal output ────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):  print(f"  {RED}✗{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{RESET} {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")


# ── Test 1: Weather API ───────────────────────────────────────────────────────

def test_weather():
    header("TEST 1: Weather API (Open-Meteo)")
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        f"&current=temperature_2m,precipitation,weathercode,cloudcover"
        f"&timezone=Asia%2FJakarta"
    )
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        c = r.json()["current"]
        ok(f"Weather API reachable — {c['temperature_2m']}°C, rain {c['precipitation']}mm, wmo={c['weathercode']}")
        return True
    except Exception as e:
        fail(f"Weather API failed: {e}")
        warn("Scraper will still work but weather metadata will be 'unknown'")
        return False


# ── Test 2 + 3: Camera reachability + frame grab ─────────────────────────────

def test_camera(cam: dict, fast: bool = False) -> dict:
    cam_id   = cam["id"]
    cam_name = cam["name"]
    url      = cam["master_url"]

    result = {
        "id": cam_id,
        "name": cam_name,
        "url_reachable": False,
        "playlist_resolved": False,
        "frame_grabbed": False,
        "frame_size": None,
        "error": None,
    }

    try:
        # Step 1: Raw HTTP reachability
        try:
            r = httpx.get(url, timeout=TIMEOUT_S, follow_redirects=True)
            if r.status_code < 500:
                result["url_reachable"] = True
            else:
                result["error"] = f"HTTP {r.status_code}"
                return result
        except Exception as e:
            result["error"] = f"Connection failed: {type(e).__name__}: {e}"
            return result

        # Step 2: Parse HLS playlist (best-effort, never crashes)
        if HAS_M3U8:
            try:
                r2 = httpx.get(url, timeout=TIMEOUT_S, follow_redirects=True)
                playlist = m3u8.loads(r2.text)
                result["playlist_resolved"] = bool(playlist.playlists or playlist.segments)
            except Exception:
                result["playlist_resolved"] = False
        else:
            result["playlist_resolved"] = None  # unknown

        if fast:
            return result

        # Step 3: Frame grab via OpenCV (same approach as working Colab code)
        cap = None
        try:
            cap = cv2.VideoCapture(url)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, TIMEOUT_S * 1000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, TIMEOUT_S * 1000)

            if cap.isOpened():
                time.sleep(2)        # let stream stabilize
                for _ in range(30):  # flush 30 frames like working Colab code
                    cap.read()
                ret, frame = cap.read()
                if ret and frame is not None:
                    h, w = frame.shape[:2]
                    result["frame_grabbed"] = True
                    result["frame_size"] = f"{w}×{h}"
        except Exception as e:
            result["error"] = str(e)
        finally:
            if cap:
                cap.release()

    except Exception as e:
        # Catch-all — test_camera should NEVER crash the outer loop
        result["error"] = f"Unexpected: {type(e).__name__}: {e}"

    return result


def test_cameras(camera_ids=None, fast=False):
    with open(CAMERAS_JSON) as f:
        cameras = json.load(f)

    if camera_ids:
        cameras = [c for c in cameras if c["id"] in camera_ids]

    header(f"TEST 2+3: Camera Streams ({len(cameras)} cameras, fast={fast})")
    print(f"  Started at {datetime.now().strftime('%H:%M:%S')}\n")

    results = []
    for cam in cameras:
        print(f"  [{cam['id']:02d}] {cam['name']} ({cam['zone']})")
        t_start = time.time()
        r = test_camera(cam, fast=fast)
        elapsed = time.time() - t_start

        if r["frame_grabbed"]:
            ok(f"Frame grabbed {r['frame_size']} in {elapsed:.1f}s")
        elif r["url_reachable"] and fast:
            ok(f"URL reachable (fast mode, no frame grab)")
        elif r["url_reachable"]:
            warn(f"URL reachable but frame grab failed — {r['error'] or 'unknown reason'}")
        else:
            fail(f"Unreachable — {r['error']}")

        results.append(r)
        time.sleep(0.3)

    return results


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: list):
    total        = len(results)
    reachable    = sum(1 for r in results if r["url_reachable"])
    frame_ok     = sum(1 for r in results if r["frame_grabbed"])
    failed_cams  = [r for r in results if not r["url_reachable"]]
    no_frame     = [r for r in results if r["url_reachable"] and not r["frame_grabbed"]]

    header("SUMMARY")
    print(f"  Total cameras tested : {total}")
    print(f"  URL reachable        : {GREEN}{reachable}{RESET}/{total}")
    print(f"  Frame grabbed        : {GREEN}{frame_ok}{RESET}/{total}")

    if failed_cams:
        print(f"\n  {RED}Unreachable cameras:{RESET}")
        for r in failed_cams:
            print(f"    [{r['id']:02d}] {r['name']} — {r['error']}")

    if no_frame:
        print(f"\n  {YELLOW}Reachable but no frame:{RESET}")
        for r in no_frame:
            print(f"    [{r['id']:02d}] {r['name']}")

    print()
    if frame_ok == total:
        print(f"  {GREEN}{BOLD}All cameras working! You're ready to run the full scraper.{RESET}")
    elif frame_ok >= total * 0.7:
        print(f"  {YELLOW}{BOLD}{frame_ok}/{total} cameras working. Good enough — failed cameras will be logged as 'failed' in metadata.csv.{RESET}")
    else:
        print(f"  {RED}{BOLD}Only {frame_ok}/{total} cameras working. Check your network / CCTV server access.{RESET}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test CCTV scraper connectivity")
    parser.add_argument("--cameras", nargs="*", type=int,
                        help="Camera IDs to test (default: all)")
    parser.add_argument("--fast", action="store_true",
                        help="Only check URL reachability, skip frame grab (much faster)")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  Malioboro Scraper — Connection Test")
    print(f"  {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print(f"{'='*55}")

    # Check dependencies
    header("Checking dependencies")
    deps = {"cv2": False, "httpx": False, "m3u8": False, "numpy": False}
    try:
        import cv2;    deps["cv2"]   = True; ok(f"opencv-python {cv2.__version__}")
    except: fail("opencv-python not installed — run: pip install opencv-python")
    try:
        import httpx;  deps["httpx"] = True; ok(f"httpx {httpx.__version__}")
    except: fail("httpx not installed — run: pip install httpx")
    try:
        import m3u8;   deps["m3u8"]  = True; ok("m3u8 installed")
    except: warn("m3u8 not installed (optional) — run: pip install m3u8")
    try:
        import numpy;  deps["numpy"] = True; ok(f"numpy {numpy.__version__}")
    except: fail("numpy not installed — run: pip install numpy")

    if not (deps["cv2"] and deps["httpx"] and deps["numpy"]):
        print(f"\n  {RED}Install missing dependencies first, then re-run this test.{RESET}\n")
        exit(1)

    test_weather()
    results = test_cameras(camera_ids=args.cameras, fast=args.fast)
    print_summary(results)
