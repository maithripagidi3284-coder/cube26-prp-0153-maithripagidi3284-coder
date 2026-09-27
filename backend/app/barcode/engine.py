"""
Barcode detection (spec sections 9, 16). pyzbar (libzbar) decodes 1D/2D
barcodes deterministically — if it decodes, the value is correct; if it
doesn't, we do NOT guess from OCR alone without flagging the disagreement.
"""
import os
import sys

if sys.platform == "win32":
    # Windows' restricted DLL search (default since Python 3.8) means
    # ctypes can load pyzbar's bundled libzbar-64.dll by full path, but
    # that DLL's own dependency (libiconv.dll, sitting right next to it)
    # won't be found unless we explicitly add the package directory as a
    # DLL search directory first. Without this, import fails with
    # "Could not find module 'libiconv.dll' (or one of its dependencies)".
    try:
        import pyzbar as _pyzbar_pkg
        _dll_dir = os.path.dirname(_pyzbar_pkg.__file__)
        os.add_dll_directory(_dll_dir)
    except Exception:
        pass  # if this fails, the import below will raise its own clear error

import uuid
import cv2
from pyzbar.pyzbar import decode as zbar_decode
from app.schemas import BarcodeResult, BoundingBox


def run_barcode_detection(image_id: str, inference_path: str) -> list[BarcodeResult]:
    img = cv2.imread(inference_path)
    if img is None:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    results: list[BarcodeResult] = []
    for attempt in (gray, cv2.equalizeHist(gray)):
        decoded = zbar_decode(attempt)
        for d in decoded:
            value = d.data.decode("utf-8", errors="replace")
            x, y, w, h = d.rect.left, d.rect.top, d.rect.width, d.rect.height
            results.append(BarcodeResult(
                region_id=str(uuid.uuid4()),
                image_id=image_id,
                symbology=d.type,
                decoded_value=value,
                bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + h),
                confidence=1.0,
            ))
        if results:
            break  # first successful pass is authoritative; don't double-count
    # de-duplicate identical (value, symbology) hits
    seen = set()
    unique = []
    for r in results:
        key = (r.decoded_value, r.symbology)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
