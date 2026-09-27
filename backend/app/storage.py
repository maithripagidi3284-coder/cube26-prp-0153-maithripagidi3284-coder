"""
Image ingestion (spec section 6). We preserve the original bytes untouched,
hash them, normalize orientation for a *separate* inference copy, and never
overwrite the original.
"""
import hashlib
import io
from pathlib import Path
from datetime import datetime
from PIL import Image as PILImage, ImageOps

from app import config


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_original(unit_id: str, image_id: str, data: bytes, content_type: str) -> dict:
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(content_type, "bin")
    unit_dir = config.STORAGE_DIR / unit_id
    unit_dir.mkdir(parents=True, exist_ok=True)
    original_path = unit_dir / f"{image_id}_original.{ext}"
    original_path.write_bytes(data)

    # Orientation-normalized inference copy (never replaces the original).
    with PILImage.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        width, height = im.size
        inference_path = unit_dir / f"{image_id}_inference.jpg"
        im.convert("RGB").save(inference_path, format="JPEG", quality=92)

    return {
        "sha256": sha256_bytes(data),
        "original_path": str(original_path),
        "inference_path": str(inference_path),
        "width": width,
        "height": height,
        "processed_at": datetime.utcnow(),
    }
