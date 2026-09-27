"""
Image quality gate (spec section 7). Purely local, deterministic, cheap —
runs before any paid VLM call so we can catch unusable captures early.
Thresholds are documented engineering heuristics, kept separate from
compliance rule logic (spec section 10).
"""
import cv2
import numpy as np
from app import config
from app.schemas import ImageQuality
from app.storage import sha256_bytes


def assess_quality(image_id: str, inference_path: str, sha256: str) -> ImageQuality:
    img = cv2.imread(inference_path)
    notes = []
    if img is None:
        return ImageQuality(
            image_id=image_id, width=0, height=0, sha256=sha256,
            blur_score=0.0, brightness=0.0, contrast=0.0, is_usable=False,
            quality_notes=["Image could not be decoded."],
        )

    height, width = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    contrast = float(np.std(gray))

    is_usable = True
    if min(width, height) < config.MIN_RESOLUTION_PX:
        notes.append(f"Resolution below minimum usable size ({width}x{height}).")
        is_usable = False
    if blur_score < config.BLUR_VARIANCE_MIN:
        notes.append(f"Image appears blurry (sharpness variance {blur_score:.1f}).")
        is_usable = False
    if brightness < config.BRIGHTNESS_MIN:
        notes.append(f"Image is too dark (mean brightness {brightness:.1f}).")
        is_usable = False
    if brightness > config.BRIGHTNESS_MAX:
        notes.append(f"Image is overexposed (mean brightness {brightness:.1f}).")
        is_usable = False

    return ImageQuality(
        image_id=image_id, width=width, height=height, sha256=sha256,
        blur_score=blur_score, brightness=brightness, contrast=contrast,
        is_usable=is_usable, quality_notes=notes,
    )
