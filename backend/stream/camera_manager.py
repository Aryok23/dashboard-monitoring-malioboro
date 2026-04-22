import logging
import threading
import time
from collections import deque
from typing import Callable

from ..inference.detector import detect, get_counts
from .hls_reader import HLSStreamReader

logger = logging.getLogger(__name__)

_VEHICLE_CLASSES = {"motorcycle", "car", "bus", "truck", "bajaj", "becak", "andong", "bicycle"}


class _ActiveCameraProcessor:
    """Dedicated thread that processes the active camera at 3 FPS."""

    def __init__(self, camera: dict, on_active_frame: Callable):
        self._camera = camera
        self._on_active_frame = on_active_frame
        self._reader = HLSStreamReader(camera["master_url"], fps_limit=3.0)
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"active-{self._camera['id']}")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._reader.stop()
        if self._thread:
            # Reader's capture worker is a daemon thread — join will return
            # quickly now that stop_event is set and the generator can exit.
            self._thread.join(timeout=5)

    @property
    def camera_id(self) -> int:
        return self._camera["id"]

    def _run(self) -> None:
        for frame in self._reader.stream_frames():
            if not self._running:
                break
            try:
                detections = detect(frame)
                self._on_active_frame(self._camera["id"], frame, detections)
            except Exception as exc:
                logger.error("Error in active frame callback: %s", exc)


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
        people_threshold: int = 50,
        vehicle_threshold: int = 30,
    ):
        self._cameras = cameras
        self._on_background_detection = on_background_detection
        self._on_background_alert = on_background_alert
        self._people_threshold = people_threshold
        self._vehicle_threshold = vehicle_threshold

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
        reader = HLSStreamReader(camera["master_url"], fps_limit=1.0)
        self._current_poll_reader = reader
        frame = None
        try:
            for f in reader.stream_frames():
                frame = f
                reader.stop()
                break
        except Exception as exc:
            logger.debug("Background poll failed for camera %d: %s", camera["id"], exc)
            reader.stop()
            return
        finally:
            self._current_poll_reader = None

        if frame is None:
            return

        try:
            detections = detect(frame)
            self._on_background_detection(camera["id"], detections)

            counts = get_counts(detections)
            people = counts.get("people", 0)
            vehicles = sum(counts.get(k, 0) for k in _VEHICLE_CLASSES)

            if people > self._people_threshold:
                self._on_background_alert(camera["id"], "HIGH_CROWD")
            if vehicles > self._vehicle_threshold:
                self._on_background_alert(camera["id"], "HIGH_TRAFFIC")
        except Exception as exc:
            logger.error("Error processing background frame for camera %d: %s", camera["id"], exc)


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
        people_threshold: int = 50,
        vehicle_threshold: int = 30,
    ):
        self._cameras = cameras
        self._on_active_frame = on_active_frame
        self._active_processor: _ActiveCameraProcessor | None = None
        self._bg_pool = _BackgroundPollingPool(
            cameras,
            on_background_detection,
            on_background_alert,
            people_threshold,
            vehicle_threshold,
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
