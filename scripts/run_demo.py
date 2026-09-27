"""
Runs the full pipeline (quality -> [VLM] -> OCR -> barcode -> geometry ->
rule engine) against the synthetic fixtures, end to end, and prints the
OverallResult for each. Proves sections 21-23 (three-valued logic) and 65-66
(decision contracts) actually work.

If ANTHROPIC_API_KEY is set, this makes REAL calls to api.anthropic.com
(spec section 29: one call per unit). If it is not set, this script
substitutes a clearly-labeled simulated VLM response so the rest of the
pipeline (which is 100% real: OpenCV geometry, Tesseract OCR, zbar barcode
decode, the deterministic rule engine) can still be demonstrated end-to-end
against real images. This substitution is ONLY in this demo script — the
actual backend (app/vision/client.py) always makes the real API call and
fails open (PENDING_REVIEW) if no key is configured, as shown by
scripts/demo_fail_open.sh.
"""
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.quality.engine import assess_quality
from app.ocr.engine import run_ocr
from app.barcode.engine import run_barcode_detection
from app.geometry.engine import analyze_region_geometry
from app.schemas import VisualEvidenceBundle, DetectedObject, ObjectType, BoundingBox
from app.rules.resolver import resolve_requirements
from app.rules.engine import evaluate
from app import config

FIXTURES = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures"
USE_REAL_VLM = bool(os.getenv("ANTHROPIC_API_KEY"))

# Simulated evidence approximates what Claude vision actually returns for
# these specific synthetic images (verified by construction: fixed pixel
# coordinates match scripts/generate_demo_images.py exactly).
SIMULATED_OBJECTS = {
    "good_unit": [
        dict(object_id="o0", type="PRODUCT", bbox_0_1000=[133, 171, 867, 829], confidence=0.95,
             description="Product package, front face", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o1", type="POLYBAG", bbox_0_1000=[111, 143, 889, 857], confidence=0.9,
             description="Translucent polybag enclosing the product", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o2", type="POLYBAG_SEAL", bbox_0_1000=[111, 190, 889, 220], confidence=0.85,
             description="Seal line near the top of the bag", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o3", type="WARNING_LABEL", bbox_0_1000=[356, 286, 689, 429], confidence=0.92,
             description="White warning label with red WARNING text", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o4", type="FNSKU_LABEL", bbox_0_1000=[389, 571, 656, 671], confidence=0.91,
             description="White FNSKU label flat on the front face", detected=True,
             visibility_state="visible", occlusion_state="none"),
    ],
    "fnsku_on_edge_unit": [
        dict(object_id="o0", type="PRODUCT", bbox_0_1000=[133, 171, 867, 829], confidence=0.95,
             description="Product package, front face", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o1", type="POLYBAG", bbox_0_1000=[111, 143, 889, 857], confidence=0.9,
             description="Translucent polybag enclosing the product", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o2", type="POLYBAG_SEAL", bbox_0_1000=[111, 190, 889, 220], confidence=0.85,
             description="Seal line near the top of the bag", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o3", type="WARNING_LABEL", bbox_0_1000=[356, 286, 689, 429], confidence=0.92,
             description="White warning label", detected=True,
             visibility_state="visible", occlusion_state="none"),
        dict(object_id="o4", type="FNSKU_LABEL", bbox_0_1000=[778, 571, 956, 671], confidence=0.88,
             description="FNSKU label positioned right at the package's right edge", detected=True,
             visibility_state="visible", occlusion_state="none"),
    ],
    "missing_polybag_unit": [
        dict(object_id="o1", type="POLYBAG", bbox_0_1000=None, confidence=0.8,
             description="No polybag visible anywhere around the product", detected=False,
             visibility_state="unknown", occlusion_state="unknown"),
        dict(object_id="o2", type="FNSKU_LABEL", bbox_0_1000=[389, 571, 656, 671], confidence=0.9,
             description="FNSKU label flat on the front face", detected=True,
             visibility_state="visible", occlusion_state="none"),
    ],
}


def simulated_extract(unit_id, image_dims):
    raw_objs = SIMULATED_OBJECTS[unit_id]
    objects = []
    (w, h) = list(image_dims.values())[0]
    for r in raw_objs:
        bbox = None
        if r["bbox_0_1000"]:
            x1, y1, x2, y2 = r["bbox_0_1000"]
            bbox = BoundingBox(x1=x1 / 1000 * w, y1=y1 / 1000 * h, x2=x2 / 1000 * w, y2=y2 / 1000 * h)
        objects.append(DetectedObject(
            object_id=r["object_id"], type=ObjectType(r["type"]), image_id="img1", bbox=bbox,
            confidence=r["confidence"], description=r["description"], detected=r["detected"],
            visibility_state=r["visibility_state"], occlusion_state=r["occlusion_state"],
        ))
    return objects, {"model": "SIMULATED (no ANTHROPIC_API_KEY in this sandbox)"}


def run_one(name, product_meta):
    path = str(FIXTURES / f"{name}.jpg")
    q = assess_quality("img1", path, "demo-sha")
    print(f"\n=== {name} === quality usable={q.is_usable} blur={q.blur_score:.0f} brightness={q.brightness:.0f}")

    if USE_REAL_VLM:
        from app.vision.client import extract_visual_evidence
        objects, raw = extract_visual_evidence(
            unit_id=name, image_paths={"img1": path}, image_dims={"img1": (q.width, q.height)},
            expected_identifiers={"fnsku": product_meta.get("fnsku")},
        )
    else:
        objects, raw = simulated_extract(name, {"img1": (q.width, q.height)})

    ocr_results = run_ocr("img1", path)
    barcode_results = run_barcode_detection("img1", path)
    package_bbox = next((o.bbox for o in objects if o.type == ObjectType.PRODUCT and o.detected and o.bbox), None)
    geometry = [analyze_region_geometry(o.object_id, o.image_id, path, o.bbox, package_bbox=package_bbox)
                for o in objects if o.detected and o.bbox is not None]

    bundle = VisualEvidenceBundle(
        unit_id=name, analysis_id=f"demo-{name}", prompt_version=config.PROMPT_VERSION,
        model_version=raw.get("model", "unknown"), image_ids=["img1"],
        image_quality=[q], objects=objects, ocr_results=ocr_results,
        barcode_results=barcode_results, geometry=geometry, raw_vlm_response=raw,
    )
    resolved = resolve_requirements(name, product_meta)
    result = evaluate(bundle, resolved, product_meta)

    print(f"OVERALL: {result.overall_status.value}")
    for c in result.checks:
        print(f"  [{c.verdict.value:9s}] {c.check_id:28s} — {c.reason}")
    return result


if __name__ == "__main__":
    print(f"Vision mode: {'REAL Claude API call' if USE_REAL_VLM else 'SIMULATED (set ANTHROPIC_API_KEY for real calls)'}")
    meta = {"prep_type": "polybag+label", "fnsku": "X0DEMO1234",
             "manufacturer_barcode": None, "expiry_required": False,
             "barcode_coverage_required": False, "handling_marks": []}

    results = {}
    for name in ["good_unit", "fnsku_on_edge_unit", "missing_polybag_unit"]:
        results[name] = run_one(name, meta)

    out = Path(__file__).resolve().parent.parent / "evaluation" / "demo_run_output.json"
    out.write_text(json.dumps({k: v.model_dump(mode="json") for k, v in results.items()}, indent=2))
    print(f"\nFull evidence-backed results written to {out}")
