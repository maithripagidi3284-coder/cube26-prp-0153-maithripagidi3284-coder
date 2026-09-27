"""
OCR engine (spec section 8). Uses Tesseract via pytesseract as the local OCR
backend for this build. (Spec suggests PaddleOCR; Tesseract is the engine
available in this environment — swapping the backend means replacing this
module only, the OCRResult contract does not change.)
"""
import os
import re
import uuid
import numpy as np
import pytesseract
import cv2
from app.schemas import OCRResult, BoundingBox
from app import config

# Resolve the Windows installer location automatically so the backend can
# find Tesseract even when the installer did not add it to PATH.
if config.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
elif os.name == "nt":
    _default_tesseract = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(_default_tesseract):
        pytesseract.pytesseract.tesseract_cmd = _default_tesseract

_WHITESPACE_RE = re.compile(r"\s+")

# Common OCR confusions worth normalizing for matching (never mutate raw_text).
_OCR_FIX_MAP = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"})


def normalize_text(raw: str) -> str:
    text = _WHITESPACE_RE.sub(" ", raw).strip()
    return text


def normalize_identifier(raw: str) -> str:
    """Aggressive normalization used only for identifier matching (FNSKU,
    barcodes), never shown to a user as 'what the OCR said'."""
    return normalize_text(raw).upper().translate(_OCR_FIX_MAP)


UPSCALE_FACTOR = 2  # warehouse label text is often small relative to full frame


def run_ocr(image_id: str, inference_path: str, min_confidence: float = 40.0) -> list[OCRResult]:
    img = cv2.imread(inference_path)
    if img is None:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Upscale + contrast enhancement (spec section 8: image enhancement for
    # OCR) — small printed labels are otherwise below Tesseract's reliable
    # character-height threshold in a full-frame photo.
    upscaled = cv2.resize(gray, None, fx=UPSCALE_FACTOR, fy=UPSCALE_FACTOR, interpolation=cv2.INTER_CUBIC)
    # Histogram equalization is a double-edged sword: it helps genuinely
    # low-contrast/dim captures but over-stretches already well-exposed
    # images and distorts thin text edges (empirically confirmed against
    # this project's fixtures). Only apply it to images with materially
    # poor contrast; leave a well-exposed capture alone.
    if float(np.std(upscaled)) < 25.0:
        enhanced = cv2.equalizeHist(upscaled)
    else:
        enhanced = upscaled

    data = pytesseract.image_to_data(enhanced, config="--psm 3", output_type=pytesseract.Output.DICT)
    results: list[OCRResult] = []
    n = len(data["text"])
    for i in range(n):
        raw = data["text"][i].strip()
        if not raw:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf < 0:
            continue
        # scale bbox back down to original image coordinates
        x = data["left"][i] / UPSCALE_FACTOR
        y = data["top"][i] / UPSCALE_FACTOR
        w = data["width"][i] / UPSCALE_FACTOR
        h = data["height"][i] / UPSCALE_FACTOR
        results.append(OCRResult(
            region_id=str(uuid.uuid4()),
            image_id=image_id,
            raw_text=raw,
            normalized_text=normalize_text(raw),
            confidence=conf / 100.0,
            bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + h),
            source="ocr_engine",
        ))
    return results
