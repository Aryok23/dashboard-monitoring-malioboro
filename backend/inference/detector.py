import logging

import numpy as np

logger = logging.getLogger(__name__)

TARGET_CLASSES = {
    "andong", "bajaj", "becak", "sepeda",
    "bus", "mobil", "motor", "orang", "truk",
}

_model = None


def load_model(model_path: str = "yolo11lbest.pt") -> None:
    global _model
    from ultralytics import YOLO
    logger.info("Loading YOLO model: %s", model_path)
    _model = YOLO(model_path)
    logger.info("YOLO model loaded.")


def detect(frame: np.ndarray) -> list[dict]:
    if _model is None:
        return []
    try:
        results = _model(frame, verbose=False)
        return _parse_results(results)
    except Exception as exc:
        logger.warning("Inference error: %s", exc)
        return []


def get_counts(detections: list[dict]) -> dict:
    counts = {cls: 0 for cls in TARGET_CLASSES}
    for det in detections:
        cls = det.get("class_name")
        if cls in counts:
            counts[cls] += 1
    return counts


def _parse_results(results) -> list[dict]:
    detections = []
    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            class_name = result.names.get(int(box.cls[0]), "")
            if class_name not in TARGET_CLASSES:
                continue
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            detections.append({
                "class_name": class_name,
                "confidence": round(float(box.conf[0]), 2),
                "bbox": [x1, y1, x2, y2],
            })
    return detections
