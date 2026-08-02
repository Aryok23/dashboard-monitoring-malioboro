import csv
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable

from ..inference.detector import detect, get_counts
from .hls_reader import HLSStreamReader

logger = logging.getLogger(__name__)

_latest_jpegs: dict[int, bytes] = {}
_latest_jpegs_lock = threading.Lock()

def evaluate_threshold_alerts(camera: dict, counts: dict) -> list[tuple[str, str]]:
    """
    Per-camera, per-class alert thresholds from cameras.json's "alert_thresholds".
    A class missing from a camera's dict never alerts for that camera — no
    fallback to a global default. Returns the (alert_type, description) pairs
    triggered this call; alert_type is always "HIGH_CROWD" (class "orang") or
    "HIGH_TRAFFIC" (any other listed class).
    """
    thresholds = camera.get("alert_thresholds") or {}
    triggered: list[tuple[str, str]] = []

    crowd_limit = thresholds.get("orang")
    people = counts.get("orang", 0)
    if crowd_limit is not None and people > crowd_limit:
        triggered.append(("HIGH_CROWD", f"orang={people} melebihi ambang {crowd_limit}"))

    exceeded = [
        f"{cls}={counts.get(cls, 0)}>{limit}"
        for cls, limit in thresholds.items()
        if cls != "orang" and counts.get(cls, 0) > limit
    ]
    if exceeded:
        triggered.append(("HIGH_TRAFFIC", "Ambang kendaraan terlampaui: " + ", ".join(exceeded)))

    return triggered


# Max time to wait for a single frame in _poll_one() before giving up on that
# camera for this round. Cooperative (calls reader.stop()), not a forced kill —
# see HLSStreamReader.stop(). Tune via env var if healthy cameras start tripping it.
_POLL_FRAME_TIMEOUT_SECONDS = float(os.environ.get("BACKGROUND_POLL_TIMEOUT_SECONDS", "10"))

# --- background poll speed logging (pure measurement, does not affect polling logic) ---
_POLL_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_POLL_LOG_PATH = os.path.join(_POLL_LOG_DIR, "background_poll_speed.csv")
_poll_log_lock = threading.Lock()
_poll_log_header_written = False


def _log_poll_speed(
    camera_id: int,
    frame_ms: float | None,
    detect_ms: float | None,
    total_ms: float,
    status: str,
) -> None:
    global _poll_log_header_written
    with _poll_log_lock:
        os.makedirs(_POLL_LOG_DIR, exist_ok=True)
        if not _poll_log_header_written:
            _poll_log_header_written = os.path.exists(_POLL_LOG_PATH)
        write_header = not _poll_log_header_written
        with open(_POLL_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(
                    ["timestamp", "camera_id", "frame_fetch_ms", "detect_ms", "total_ms", "status"]
                )
            writer.writerow([
                datetime.now().isoformat(timespec="milliseconds"),
                camera_id,
                f"{frame_ms:.3f}" if frame_ms is not None else "",
                f"{detect_ms:.3f}" if detect_ms is not None else "",
                f"{total_ms:.3f}",
                status,
            ])
        _poll_log_header_written = True


class _ActiveCameraProcessor:
    """Two threads: one drains the HLS stream, one runs inference at a fixed interval."""

    def __init__(self, camera: dict, on_active_frame: Callable, detection_interval: float = 0.5):
        self._camera = camera
        self._on_active_frame = on_active_frame
        self._detection_interval = detection_interval
        self._reader = HLSStreamReader(camera["master_url"], fps_limit=30.0)
        self._reader_thread: threading.Thread | None = None
        self._inference_thread: threading.Thread | None = None
        self._running = False
        self._latest_frame = None
        self._frame_lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name=f"active-reader-{self._camera['id']}"
        )
        self._inference_thread = threading.Thread(
            target=self._inference_loop, daemon=True, name=f"active-infer-{self._camera['id']}"
        )
        self._reader_thread.start()
        self._inference_thread.start()

    def stop(self) -> None:
        self._running = False
        self._reader.stop()
        if self._reader_thread:
            self._reader_thread.join(timeout=5)
        if self._inference_thread:
            self._inference_thread.join(timeout=5)

    @property
    def camera_id(self) -> int:
        return self._camera["id"]

    def _reader_loop(self) -> None:
        for frame in self._reader.stream_frames():
            if not self._running:
                break
            with self._frame_lock:
                self._latest_frame = frame

    def _inference_loop(self) -> None:
        import os
        import cv2
        if os.environ.get("DEBUG_SAVE_FRAMES") == "1":
            os.makedirs(
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "debug_frames",
                ),
                exist_ok=True,
            )
        next_tick = time.monotonic()
        while self._running:
            next_tick += self._detection_interval

            with self._frame_lock:
                frame = self._latest_frame

            if frame is not None:
                try:
                    t0 = time.monotonic()
                    detections = detect(frame, camera_id=self._camera['id'])
                    logger.debug("cam %d inference=%.3fs", self._camera['id'], time.monotonic() - t0)
                    ok, buf = cv2.imencode('.jpg', frame)
                    if ok:
                        with _latest_jpegs_lock:
                            _latest_jpegs[self._camera['id']] = buf.tobytes()
                    self._on_active_frame(self._camera["id"], frame, detections)
                    if os.environ.get("DEBUG_SAVE_FRAMES") == "1":
                        debug_dir = os.path.join(
                            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "debug_frames",
                        )
                        ts_ms = int(time.time() * 1000)
                        fname = f"{self._camera['id']}_{ts_ms}.jpg"
                        debug_frame = frame.copy()
                        for det in detections:
                            x1, y1, x2, y2 = det["bbox"]
                            label = f"{det['class_name']} {det['confidence']}"
                            cv2.rectangle(debug_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            cv2.putText(
                                debug_frame, label, (x1, max(y1 - 5, 0)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                            )
                        cv2.imwrite(os.path.join(debug_dir, fname), debug_frame)
                except Exception as exc:
                    logger.error("Error in active frame callback: %s", exc)

            sleep_time = next_tick - time.monotonic()
            logger.debug("cam %d sleep=%.3fs", self._camera['id'], sleep_time)
            if sleep_time > 0:
                time.sleep(sleep_time)


class _BackgroundPollingPool:
    """
    Cycles through all non-active cameras one at a time.
    Opens one stream per slot, grabs a single frame, runs inference, closes it.
    Each camera gets ~60 s between samples.
    """

    def __init__(
        self,
        cameras: list[dict],
        on_background_detection: Callable,
        on_background_alert: Callable,
    ):
        self._cameras = cameras
        self._on_background_detection = on_background_detection
        self._on_background_alert = on_background_alert

        self._active_camera_id: int | None = None
        self._queue: deque[int] = deque()
        self._thread: threading.Thread | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._current_poll_reader: HLSStreamReader | None = None

    def set_active_camera(self, camera_id: int) -> None:
        self._active_camera_id = camera_id
        non_active = [c["id"] for c in self._cameras if c["id"] != camera_id]
        self._queue = deque(non_active)

    def start(self) -> None:
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="bg-pool")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        # Cancel any in-progress poll so its cap.read() doesn't hold up shutdown.
        if self._current_poll_reader:
            self._current_poll_reader.stop()
        if self._thread:
            self._thread.join(timeout=5)

    def _camera_by_id(self, camera_id: int) -> dict | None:
        return next((c for c in self._cameras if c["id"] == camera_id), None)

    def _run(self) -> None:
        while self._running:
            if not self._queue:
                self._stop_event.wait(1.0)
                continue

            camera_id = self._queue.popleft()

            if camera_id == self._active_camera_id:
                continue

            camera = self._camera_by_id(camera_id)
            if camera is None:
                continue

            self._poll_one(camera)

            if not self._running:
                break

            self._queue.append(camera_id)

            n = max(len(self._queue), 1)
            self._stop_event.wait(max(1.0, 60.0 / n))

    def _poll_one(self, camera: dict) -> None:
        t_start = time.perf_counter()
        frame_ms: float | None = None
        detect_ms: float | None = None

        reader = HLSStreamReader(camera["master_url"], fps_limit=1.0)
        self._current_poll_reader = reader
        frame = None
        t_frame_start = time.perf_counter()

        timed_out = threading.Event()

        def _on_frame_timeout() -> None:
            timed_out.set()
            reader.stop()

        timeout_timer = threading.Timer(_POLL_FRAME_TIMEOUT_SECONDS, _on_frame_timeout)
        timeout_timer.start()
        try:
            for f in reader.stream_frames():
                frame = f
                frame_ms = (time.perf_counter() - t_frame_start) * 1000
                reader.stop()
                break
        except Exception as exc:
            logger.debug("Background poll failed for camera %d: %s", camera["id"], exc)
            reader.stop()
            status = "timeout" if timed_out.is_set() else "frame_error"
            _log_poll_speed(
                camera["id"], frame_ms, detect_ms, (time.perf_counter() - t_start) * 1000, status
            )
            return
        finally:
            timeout_timer.cancel()
            self._current_poll_reader = None

        if frame is None:
            status = "timeout" if timed_out.is_set() else "no_frame"
            _log_poll_speed(
                camera["id"], frame_ms, detect_ms, (time.perf_counter() - t_start) * 1000, status
            )
            return

        status = "ok"
        try:
            t_detect_start = time.perf_counter()
            detections = detect(frame, camera_id=camera["id"])
            detect_ms = (time.perf_counter() - t_detect_start) * 1000
            self._on_background_detection(camera["id"], detections)

            counts = get_counts(detections)
            for alert_type, description in evaluate_threshold_alerts(camera, counts):
                self._on_background_alert(
                    camera["id"], alert_type, description, frame, detections, counts
                )
        except Exception as exc:
            logger.error("Error processing background frame for camera %d: %s", camera["id"], exc)
            status = "processing_error"

        _log_poll_speed(
            camera["id"], frame_ms, detect_ms, (time.perf_counter() - t_start) * 1000, status
        )


class CameraManager:
    """
    Public façade that owns the active-camera processor and background pool.

    Callbacks are called from background threads — callers must be thread-safe.
    """

    def __init__(
        self,
        cameras: list[dict],
        on_active_frame: Callable,
        on_background_detection: Callable,
        on_background_alert: Callable,
    ):
        self._cameras = cameras
        self._on_active_frame = on_active_frame
        self._active_processor: _ActiveCameraProcessor | None = None
        self._bg_pool = _BackgroundPollingPool(
            cameras,
            on_background_detection,
            on_background_alert,
        )

    def start(self, default_camera_id: int = 1) -> None:
        camera = self._camera_by_id(default_camera_id)
        if camera is None:
            raise ValueError(f"Camera id={default_camera_id} not found in cameras list")

        self._bg_pool.set_active_camera(default_camera_id)
        self._bg_pool.start()

        self._active_processor = _ActiveCameraProcessor(camera, self._on_active_frame)
        self._active_processor.start()
        logger.info("CameraManager started with active camera id=%d", default_camera_id)

    def switch_active_camera(self, camera_id: int) -> None:
        camera = self._camera_by_id(camera_id)
        if camera is None:
            raise ValueError(f"Camera id={camera_id} not found in cameras list")

        if self._active_processor:
            self._active_processor.stop()

        self._bg_pool.set_active_camera(camera_id)

        self._active_processor = _ActiveCameraProcessor(camera, self._on_active_frame)
        self._active_processor.start()
        logger.info("Active camera switched to id=%d", camera_id)

    def stop(self) -> None:
        if self._active_processor:
            self._active_processor.stop()
        self._bg_pool.stop()
        logger.info("CameraManager stopped.")

    def _camera_by_id(self, camera_id: int) -> dict | None:
        return next((c for c in self._cameras if c["id"] == camera_id), None)
