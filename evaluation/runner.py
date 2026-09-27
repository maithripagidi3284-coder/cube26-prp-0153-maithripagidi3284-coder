"""
Evaluation runner (spec sections 38, 39, 42). Loads the dataset manifest,
runs each unit through the exact same pipeline the API uses (no
evaluation-only shortcuts), scores it against ground truth, and writes a
failure-mode breakdown.

Usage:
    python evaluation/runner.py                # uses simulated VLM (no key)
    ANTHROPIC_API_KEY=... python evaluation/runner.py   # real VLM calls
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.quality.engine import assess_quality
from app.ocr.engine import run_ocr
from app.barcode.engine import run_barcode_detection
from app.geometry.engine import analyze_region_geometry
from app.schemas import VisualEvidenceBundle, ObjectType
from app.rules.resolver import resolve_requirements
from app.rules.engine import evaluate
from app import config
sys.path.insert(0, str(ROOT))
from evaluation.metrics import score_run
from run_demo import simulated_extract, USE_REAL_VLM

MANIFEST = ROOT / "evaluation" / "dataset" / "manifest.json"


def run_unit(unit_id: str, image_paths: list[str], product_meta: dict):
    path = str(ROOT / image_paths[0])  # demo manifest is single-image per unit
    q = assess_quality("img1", path, "eval-sha")

    if USE_REAL_VLM:
        from app.vision.client import extract_visual_evidence
        objects, raw = extract_visual_evidence(
            unit_id=unit_id, image_paths={"img1": path}, image_dims={"img1": (q.width, q.height)},
            expected_identifiers={"fnsku": product_meta.get("fnsku")},
        )
    else:
        objects, raw = simulated_extract(unit_id, {"img1": (q.width, q.height)})

    ocr_results = run_ocr("img1", path)
    barcode_results = run_barcode_detection("img1", path)
    package_bbox = next((o.bbox for o in objects if o.type == ObjectType.PRODUCT and o.detected and o.bbox), None)
    geometry = [analyze_region_geometry(o.object_id, o.image_id, path, o.bbox, package_bbox=package_bbox)
                for o in objects if o.detected and o.bbox is not None]

    bundle = VisualEvidenceBundle(
        unit_id=unit_id, analysis_id=f"eval-{unit_id}", prompt_version=config.PROMPT_VERSION,
        model_version=raw.get("model", "unknown"), image_ids=["img1"], image_quality=[q],
        objects=objects, ocr_results=ocr_results, barcode_results=barcode_results,
        geometry=geometry, raw_vlm_response=raw,
    )
    resolved = resolve_requirements(unit_id, product_meta)
    return evaluate(bundle, resolved, product_meta)


def failure_analysis(predictions, ground_truth, results_by_unit):
    """Spec section 42 — structured report, not just numbers."""
    failures = []
    for unit_id, truth_checks in ground_truth.items():
        pred_checks = predictions.get(unit_id, {})
        for check_id, truth in truth_checks.items():
            pred = pred_checks.get(check_id)
            if pred is not None and pred != truth:
                check_result = next((c for c in results_by_unit[unit_id].checks if c.check_id == check_id), None)
                failures.append({
                    "unit_id": unit_id, "check_id": check_id,
                    "ground_truth": truth, "predicted": pred,
                    "system_reason": check_result.reason if check_result else None,
                    "likely_cause": _guess_cause(check_id, truth, pred),
                })
    return failures


def _guess_cause(check_id, truth, pred):
    if pred == "UNCERTAIN":
        return "Evidence extraction (OCR/VLM confidence) insufficient to reach a decisive verdict."
    if truth == "FAIL" and pred == "PASS":
        return "Possible missed detection — highest-priority failure mode to investigate."
    if truth == "PASS" and pred == "FAIL":
        return "Possible over-sensitive geometry/OCR heuristic — check threshold tuning."
    return "Unclassified disagreement."


if __name__ == "__main__":
    manifest = json.loads(MANIFEST.read_text())
    predictions, ground_truth, results_by_unit = {}, {}, {}

    print(f"Vision mode: {'REAL' if USE_REAL_VLM else 'SIMULATED'}")
    for unit in manifest["units"]:
        result = run_unit(unit["unit_id"], unit["image_paths"], unit["product_meta"])
        results_by_unit[unit["unit_id"]] = result
        predictions[unit["unit_id"]] = {c.check_id: c.verdict.value for c in result.checks}
        ground_truth[unit["unit_id"]] = unit["ground_truth"]

    scores = score_run(predictions, ground_truth)
    failures = failure_analysis(predictions, ground_truth, results_by_unit)

    report = {"scores": scores, "failures": failures}
    out_path = ROOT / "evaluation" / "latest_report.json"
    out_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(scores["_overall"], indent=2))
    print(f"\n{len(failures)} disagreement(s) with ground truth — see evaluation/latest_report.json")
    print(f"Full report written to {out_path}")
