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

# COCO class names → our class names
# Classes not listed here are ignored by the detector
_COCO_TO_OURS = {
    "person":     "people",
    "bicycle":    "bicycle",
    "motorcycle": "motorcycle",
    "car":        "car",
    "bus":        "bus",
    "truck":      "truck",
    # bajaj, becak, andong are not in COCO — need fine-tuned weights
}

_model = None


def load_model(model_path: str = "yolov8n.pt") -> None:
    """
    Load a YOLO model.
    Pass "yolov8n.pt" (or s/m/l/x) to use pretrained COCO weights —
    ultralytics downloads the file automatically on first run.
    Pass a custom "best.pt" path for fine-tuned weights.
    """
    global _model
    from ultralytics import YOLO
    logger.info("Loading YOLO model: %s", model_path)
    _model = YOLO(model_path)
    logger.info("YOLO model loaded. Detectable classes: %s", list(_COCO_TO_OURS.keys()))


def detect(frame: np.ndarray) -> list[dict]:
    """
    Run detection on a frame.
    Returns list of dicts: {class_name, confidence, bbox: [x1,y1,x2,y2]}.
    Falls back to empty list if model is not loaded.
    """
    if _model is None:
        return []

    try:
        results = _model(frame, verbose=False)
        return _parse_results(results)
    except Exception as exc:
        logger.warning("Inference error: %s", exc)
        return []


def get_counts(detections: list[dict]) -> dict:
    """Return per-class count dict from a list of detections."""
    counts = {cls: 0 for cls in TARGET_CLASSES}
    for det in detections:
        cls = det.get("class_name")
        if cls in counts:
            counts[cls] += 1
    return counts


def _parse_results(results) -> list[dict]:
    """Convert ultralytics Results objects to our detection dict format."""
    detections = []
    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            coco_name = result.names.get(int(box.cls[0]), "")
            our_name = _COCO_TO_OURS.get(coco_name)
            if our_name is None:
                continue  # class not relevant to us
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            detections.append({
                "class_name": our_name,
                "confidence": round(float(box.conf[0]), 2),
                "bbox": [x1, y1, x2, y2],
            })
    return detections
