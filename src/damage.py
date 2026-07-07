"""Visual damage detection using a YOLOv11 segmentation model.

Wraps the pre-trained car-damage model (harpreetsahota/car-dd-segmentation-yolov11)
trained on the CarDD benchmark dataset, and exposes a clean `detect_damage()`
function for the Gradio demo.

Raw classes (6): crack, dent, glass shatter, lamp broken, scratch, tire flat.
Mapped to business categories so pricing logic is stable to model swaps.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from ultralytics import YOLO

ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "damage_yolo.pt"

# Drop detections below this confidence — below ~0.4 the model hallucinates.
CONF_THRESHOLD = 0.40

# Reject images smaller than this (likely icons / broken uploads).
MIN_IMAGE_DIM = 120

# Upscale anything smaller than this to YOLO's native input size before inference.
# Saves the user from having to find high-res photos for the demo.
TARGET_INFERENCE_DIM = 640

# Map raw model classes → business categories used by the pricing logic.
# Pricing applies the deduction ONCE per category, regardless of how many
# raw-class instances are detected within it.
CLASS_TO_CATEGORY = {
    "crack":         "crack",
    "dent":          "dent",
    "glass shatter": "glass_damage",
    "lamp broken":   "lamp_damage",
    "scratch":       "scratch",
    "tire flat":     "tire_damage",
}

CATEGORY_HUMAN = {
    "crack":        "panel/glass crack",
    "dent":         "panel dent",
    "glass_damage": "shattered windscreen / glass",
    "lamp_damage":  "broken headlamp / taillight",
    "scratch":      "scratch",
    "tire_damage":  "flat / damaged tire",
}


def _load_model() -> YOLO:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Damage model missing at {MODEL_PATH}. "
            "Download from huggingface vineetsarpal/yolov11n-car-damage."
        )
    return YOLO(str(MODEL_PATH))


# Module-level singleton — load once per process.
_MODEL = _load_model()


def _quality_check(img: Image.Image) -> tuple[bool, str]:
    """Reject blank / icon-tiny images before wasting an inference."""
    w, h = img.size
    if w < MIN_IMAGE_DIM or h < MIN_IMAGE_DIM:
        return False, f"Image too small ({w}x{h}). Please upload a photo at least {MIN_IMAGE_DIM}x{MIN_IMAGE_DIM}."
    arr = np.asarray(img.convert("RGB"))
    # If 80%+ of pixels are within 5 of the mean, image is likely blank/solid colour.
    diffs = np.abs(arr - arr.mean(axis=(0, 1))).mean(axis=2)
    if (diffs < 5).sum() / diffs.size > 0.80:
        return False, "Image looks blank or low-contrast. Please upload a clearer car photo."
    return True, ""


def _upscale_if_small(img: Image.Image) -> Image.Image:
    """If the image is smaller than YOLO's native input on either dim, scale it up
    proportionally. Lanczos keeps edges sharp enough for damage detection."""
    w, h = img.size
    short = min(w, h)
    if short >= TARGET_INFERENCE_DIM:
        return img
    scale = TARGET_INFERENCE_DIM / short
    new_size = (int(w * scale), int(h * scale))
    return img.resize(new_size, Image.LANCZOS)


def detect_damage(image: Image.Image | np.ndarray | str) -> dict[str, Any]:
    """Run damage detection on one image.

    Args:
        image: PIL image, numpy array (HxWx3 RGB), or filepath.

    Returns:
        {
          "ok": bool,                          # False if image was rejected
          "message": str,                      # rejection reason if ok=False
          "categories_detected": list[str],    # business categories (deduped)
          "raw_class_counts": dict[str, int],  # per-raw-class instance counts
          "annotated_image": np.ndarray,       # RGB image with boxes drawn
        }
    """
    # Normalize input → PIL
    if isinstance(image, str):
        image = Image.open(image)
    elif isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    if image.mode != "RGB":
        image = image.convert("RGB")

    ok, msg = _quality_check(image)
    if not ok:
        return {
            "ok": False,
            "message": msg,
            "categories_detected": [],
            "raw_class_counts": {},
            "annotated_image": np.asarray(image),
        }

    inference_img = _upscale_if_small(image)
    results = _MODEL.predict(
        source=inference_img,
        conf=CONF_THRESHOLD,
        iou=0.55,           # NMS — collapses heavily-overlapping boxes of same class
        verbose=False,
    )
    r = results[0]

    # added by swati mishra on 01072026 for damage inspection report
    damage_items = []
    confidences = []

    img_w, img_h = inference_img.size
    total_img_area = img_w * img_h
    total_damage_area = 0

    # raw_counts: dict[str, int] = defaultdict(int)
    # for cls_idx in r.boxes.cls.cpu().numpy().astype(int):
    #     raw_counts[_MODEL.names[cls_idx]] += 1

    # added by swati mishra on 01072026 for damage inspection report
    raw_counts: dict[str, int] = defaultdict(int)

    if r.boxes is not None:
        boxes = r.boxes.xyxy.cpu().numpy()
        classes = r.boxes.cls.cpu().numpy().astype(int)
        confs = r.boxes.conf.cpu().numpy()

        for box, cls_idx, conf in zip(boxes, classes, confs):
            raw_class = _MODEL.names[cls_idx]
            raw_counts[raw_class] += 1
            confidences.append(float(conf))

            x1, y1, x2, y2 = box
            box_area = max(0, x2 - x1) * max(0, y2 - y1)
            total_damage_area += box_area

            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            if cy < img_h * 0.33:
                vertical = "upper"
            elif cy > img_h * 0.66:
                vertical = "lower"
            else:
                vertical = "middle"

            if cx < img_w * 0.33:
                horizontal = "left"
            elif cx > img_w * 0.66:
                horizontal = "right"
            else:
                horizontal = "center"

            damage_items.append({
                "damage_type": CATEGORY_HUMAN.get(CLASS_TO_CATEGORY.get(raw_class, raw_class), raw_class),
                "raw_class": raw_class,
                "confidence": round(float(conf) * 100, 1),
                "location": f"{vertical} {horizontal} area",
            })

    categories: set[str] = set()
    for raw_class in raw_counts:
        cat = CLASS_TO_CATEGORY.get(raw_class)
        if cat:
            categories.add(cat)

    annotated = r.plot()[..., ::-1]  # BGR → RGB (ultralytics returns BGR)

    # added by swati mishra on 01072026 for damage inspection report
    damage_area_ratio = total_damage_area / total_img_area if total_img_area else 0

    if not raw_counts:
        severity = "None"
        confidence = 0.0
        repair_cost_category = "None"
        inspection_score = 100
        recommendation = "No visible damage detected in the uploaded image."
    elif damage_area_ratio < 0.03:
        severity = "Minor"
        confidence = round(sum(confidences) / len(confidences) * 100, 1)
        repair_cost_category = "Low"
        inspection_score = 95
        recommendation = "Minor visible damage detected. Cosmetic repair may be required."
    elif damage_area_ratio < 0.10:
        severity = "Moderate"
        confidence = round(sum(confidences) / len(confidences) * 100, 1)
        repair_cost_category = "Medium"
        inspection_score = 88
        recommendation = "Visible body damage detected. Professional inspection is recommended."
    else:
        severity = "Severe"
        confidence = round(sum(confidences) / len(confidences) * 100, 1)
        repair_cost_category = "High"
        inspection_score = 75
        recommendation = "Major visible damage detected. Detailed physical inspection is strongly recommended."

    # return {
    #     "ok": True,
    #     "message": "",
    #     "categories_detected": sorted(categories),
    #     "raw_class_counts": dict(raw_counts),
    #     "annotated_image": annotated,
    # }

    return {
        "ok": True,
        "message": "",
        "categories_detected": sorted(categories),
        "raw_class_counts": dict(raw_counts),
        "annotated_image": annotated,
        "severity": severity,
        "confidence": confidence,
        "damage_location": damage_items[0]["location"] if damage_items else None,
        "damage_items": damage_items,
        "repair_cost_category": repair_cost_category,
        "inspection_score": inspection_score,
        "recommendation": recommendation,
    }
