"""
Orchestration (spec section 3 data flow, section 29 one-call requirement).
This is the only place that calls every subsystem, in order, and merges
their outputs into a single VisualEvidenceBundle before handing off to the
deterministic rule engine.
"""
import uuid
import logging
from datetime import datetime

from app.schemas import VisualEvidenceBundle, OverallResult, Verdict, ComplianceCheckResult, EvidenceItem
from app.quality.engine import assess_quality
from app.ocr.engine import run_ocr
from app.barcode.engine import run_barcode_detection
from app.geometry.engine import analyze_region_geometry
from app.vision.client import extract_visual_evidence, VisionUnavailableError
from app.vision.prompts import PROMPT_VERSION
from app.rules.resolver import resolve_requirements
from app.rules.engine import evaluate
from app import config

logger = logging.getLogger("prep_manager.pipeline")


class PipelinePendingReview(Exception):
    """Fail-open signal (spec sections 28, 64): processing could not
    complete, unit must be routed to human review, nothing is fabricated."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def run_analysis(unit_id: str, images: list[dict], product_meta: dict) -> OverallResult:
    """
    images: list of {"image_id": str, "inference_path": str, "sha256": str}
    product_meta: dict as expected by rules.resolver.resolve_requirements,
                  plus optional "manufacturer_barcode".
    """
    analysis_id = str(uuid.uuid4())

    # 1. Image quality gate (local, cheap, before any paid call).
    quality_results = []
    image_dims = {}
    for img in images:
        q = assess_quality(img["image_id"], img["inference_path"], img["sha256"])
        quality_results.append(q)
        image_dims[img["image_id"]] = (q.width, q.height)

    if all(not q.is_usable for q in quality_results):
        raise PipelinePendingReview("All supplied images failed the quality gate (blur/exposure/resolution).")

    # 2. ONE multimodal VLM call per unit (spec section 29) — evidence only.
    image_paths = {img["image_id"]: img["inference_path"] for img in images}
    try:
        objects, raw_response = extract_visual_evidence(
            unit_id=unit_id, image_paths=image_paths, image_dims=image_dims,
            expected_identifiers={
                "fnsku": product_meta.get("fnsku"),
                "manufacturer_barcode": product_meta.get("manufacturer_barcode"),
            },
        )
        model_version = raw_response.get("model", config.VISION_MODEL)
    except VisionUnavailableError as exc:
        logger.warning("Vision call failed for unit %s: %s", unit_id, exc)
        raise PipelinePendingReview(f"Vision model unavailable: {exc}") from exc

    # 3. Local deterministic evidence: OCR + barcode + geometry, per image.
    ocr_results, barcode_results, geometry_results = [], [], []
    for img in images:
        ocr_results.extend(run_ocr(img["image_id"], img["inference_path"]))
        barcode_results.extend(run_barcode_detection(img["image_id"], img["inference_path"]))

    # Use the detected PRODUCT/package region (if any) per image as the
    # boundary reference for edge-proximity geometry — see geometry/engine.py.
    from app.schemas import ObjectType
    package_bbox_by_image = {}
    for obj in objects:
        if obj.type == ObjectType.PRODUCT and obj.detected and obj.bbox is not None:
            package_bbox_by_image[obj.image_id] = obj.bbox

    for obj in objects:
        if obj.detected and obj.bbox is not None:
            geometry_results.append(
                analyze_region_geometry(
                    obj.object_id, obj.image_id, image_paths[obj.image_id], obj.bbox,
                    package_bbox=package_bbox_by_image.get(obj.image_id),
                )
            )

    bundle = VisualEvidenceBundle(
        unit_id=unit_id, analysis_id=analysis_id, prompt_version=PROMPT_VERSION,
        model_version=model_version, image_ids=list(image_paths.keys()),
        image_quality=quality_results, objects=objects, ocr_results=ocr_results,
        barcode_results=barcode_results, geometry=geometry_results,
        raw_vlm_response=raw_response,
    )

    # 4. Resolve requirements (independent of the VLM call — never invented
    # by the model) and run the deterministic rule engine.
    resolved = resolve_requirements(unit_id, product_meta)
    result = evaluate(bundle, resolved, product_meta)
    return result, bundle
