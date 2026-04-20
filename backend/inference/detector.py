import random
import logging

import numpy as np

logger = logging.getLogger(__name__)

TARGET_CLASSES = [
    "people",
    "bicycle",
    "motorcycle",
    "bajaj",
    "becak",
    "andong",
    "car",
    "bus",
    "truck",
]

_model = None


def load_model(model_path: str) -> None:
    """Load YOLO model from path. Stub does nothing until real weights are provided."""
    global _model
    logger.info(f"Loading model from {model_path}")
    # Uncomment when real weights are available:
    # from ultralytics import YOLO
    # _model = YOLO(model_path)
    # logger.info("Model loaded successfully.")


def detect(frame: np.ndarray) -> list[dict]:
    """
    Run detection on a frame.
    Returns a list of dicts: {class_name, confidence, bbox: [x1,y1,x2,y2]}.
    """
    if _model is not None:
        # Real inference path (uncomment when model is loaded):
        # results = _model(frame, verbose=False)
        # return _parse_yolo_results(results)
        pass

    # Stub: return realistic random detections for UI/DB testing
    h, w = (frame.shape[:2] if frame is not None and frame.size > 0 else (480, 640))
    num_detections = random.randint(2, 8)
    detections = []

    for _ in range(num_detections):
        x1 = random.randint(0, max(0, w - 50))
        y1 = random.randint(0, max(0, h - 50))
        x2 = random.randint(x1 + 20, min(x1 + 200, w))
        y2 = random.randint(y1 + 20, min(y1 + 200, h))

        detections.append(
            {
                "class_name": random.choice(TARGET_CLASSES),
                "confidence": round(random.uniform(0.60, 0.95), 2),
                "bbox": [x1, y1, x2, y2],
            }
        )

    return detections


def get_counts(detections: list[dict]) -> dict:
    """Return per-class count dict from a list of detections."""
    counts = {cls: 0 for cls in TARGET_CLASSES}
    for det in detections:
        cls = det.get("class_name")
        if cls in counts:
            counts[cls] += 1
    return counts


def _parse_yolo_results(results) -> list[dict]:
    """Convert ultralytics Results to detection dicts. Used when real model is loaded."""
    detections = []
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        for box in boxes:
            class_id = int(box.cls[0])
            class_name = result.names.get(class_id, "unknown")
            if class_name not in TARGET_CLASSES:
                continue
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            conf = float(box.conf[0])
            detections.append(
                {
                    "class_name": class_name,
                    "confidence": round(conf, 2),
                    "bbox": [x1, y1, x2, y2],
                }
            )
    return detections
