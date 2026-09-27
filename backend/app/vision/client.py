"""
Vision evidence extraction client.

Uses exactly one multimodal Groq API call per unit, carrying every image and
requirement context. This module never returns a compliance verdict — only
DetectedObject evidence. The rule engine is the only place PASS/FAIL/UNCERTAIN
is decided.
"""
import base64
import json
import mimetypes
import re
import uuid

import httpx

from app import config
from app.schemas import BoundingBox, DetectedObject, ObjectType
from app.vision.prompts import SYSTEM_PROMPT, build_user_prompt, PROMPT_VERSION

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class VisionUnavailableError(Exception):
    """Raised when the VLM call fails; callers fail-open to review."""


def _encode_image(path: str) -> tuple[str, str]:
    media_type = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return media_type, data


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise VisionUnavailableError("Model response did not contain a JSON object.")
    return json.loads(text[start:end + 1])


def extract_visual_evidence(
    unit_id: str,
    image_paths: dict[str, str],
    image_dims: dict[str, tuple[int, int]],
    expected_identifiers: dict,
) -> tuple[list[DetectedObject], dict]:
    """Return (detected_objects, raw_response_json)."""
    if not config.GROQ_API_KEY:
        raise VisionUnavailableError("GROQ_API_KEY is not configured.")

    image_ids = list(image_paths.keys())
    if len(image_ids) > 3:
        raise VisionUnavailableError(
            "Qwen 3.8 27B accepts a maximum of 3 images per vision request."
        )

    content = []
    for iid in image_ids:
        media_type, data = _encode_image(image_paths[iid])
        content.append({"type": "text", "text": f"[image_id: {iid}]"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{data}"},
        })

    content.append({
        "type": "text",
        "text": build_user_prompt(unit_id, image_ids, expected_identifiers),
    })

    payload = {
        "model": config.VISION_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "max_completion_tokens": config.VISION_MAX_TOKENS,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(GROQ_URL, headers=headers, json=payload, timeout=90.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise VisionUnavailableError(f"Vision API call failed: {exc}") from exc

    choices = data.get("choices", [])
    if not choices:
        raise VisionUnavailableError("Groq API returned no choices.")
    message = choices[0].get("message", {})
    text = message.get("content")
    if not text:
        raise VisionUnavailableError("Groq API returned no text content.")

    try:
        parsed = _extract_json(text)
    except (json.JSONDecodeError, VisionUnavailableError) as exc:
        raise VisionUnavailableError(f"Could not parse model JSON: {exc}") from exc

    objects: list[DetectedObject] = []
    for raw_obj in parsed.get("objects", []):
        try:
            obj_type = ObjectType(raw_obj["type"])
        except (KeyError, ValueError):
            continue
        image_id = raw_obj.get("image_id")
        if image_id not in image_dims:
            continue

        bbox = None
        box = raw_obj.get("bbox_0_1000")
        if box and len(box) == 4:
            w, h = image_dims[image_id]
            x1, y1, x2, y2 = box
            bbox = BoundingBox(
                x1=x1 / 1000.0 * w,
                y1=y1 / 1000.0 * h,
                x2=x2 / 1000.0 * w,
                y2=y2 / 1000.0 * h,
            )

        objects.append(DetectedObject(
            object_id=raw_obj.get("object_id") or str(uuid.uuid4()),
            type=obj_type,
            image_id=image_id,
            bbox=bbox,
            confidence=float(raw_obj.get("confidence", 0.0)),
            description=raw_obj.get("description", ""),
            ocr_text=raw_obj.get("ocr_text_guess"),
            visibility_state=raw_obj.get("visibility_state", "unknown"),
            occlusion_state=raw_obj.get("occlusion_state", "unknown"),
            spatial_relationships=raw_obj.get("spatial_relationships", []) or [],
            detected=bool(raw_obj.get("detected", True)),
        ))

    return objects, {"model": data.get("model"), "raw": parsed}
