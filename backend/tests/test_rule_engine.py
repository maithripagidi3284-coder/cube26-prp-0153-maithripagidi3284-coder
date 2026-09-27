"""
Rule-engine tests (spec sections 40, 57). These construct VisualEvidenceBundle
objects directly (no VLM/network call needed) so they're fast, deterministic,
and runnable offline — exactly the kind of test the spec asks for: PASS
evidence, FAIL evidence, UNCERTAIN evidence, per check.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import (
    VisualEvidenceBundle, DetectedObject, ObjectType, BoundingBox,
    ImageQuality, OCRResult, BarcodeResult, GeometryEvidence, Verdict,
)
from app.rules.resolver import ResolvedRequirement
from app.rules import engine as rule_engine


def _rule(check, name="Test rule"):
    return ResolvedRequirement(
        rule_id=check.upper(), version="RULES_V1", name=name,
        visual_verifiability="VISUAL", check=check,
        source_name="TEST_SOURCE", source_url=None, source_version="1",
    )


def _bundle(**kwargs):
    defaults = dict(
        unit_id="u1", analysis_id="a1", prompt_version="VISION_PROMPT_V1",
        model_version="test-model", image_ids=["img1"],
        image_quality=[ImageQuality(image_id="img1", width=900, height=700, sha256="x",
                                     blur_score=200, brightness=120, contrast=50, is_usable=True)],
        objects=[], ocr_results=[], barcode_results=[], geometry=[],
    )
    defaults.update(kwargs)
    return VisualEvidenceBundle(**defaults)


# --------------------------------------------------------------- polybag ---
def test_polybag_pass():
    bundle = _bundle(objects=[
        DetectedObject(object_id="o1", type=ObjectType.POLYBAG, image_id="img1",
                        bbox=BoundingBox(x1=100, y1=100, x2=800, y2=600), confidence=0.9,
                        description="Sealed polybag", detected=True),
        DetectedObject(object_id="o2", type=ObjectType.POLYBAG_SEAL, image_id="img1",
                        bbox=BoundingBox(x1=100, y1=140, x2=800, y2=160), confidence=0.85,
                        description="Seal line", detected=True),
    ])
    result = rule_engine.check_polybag(bundle, _rule("polybag"))
    assert result.verdict == Verdict.PASS


def test_polybag_fail_missing():
    bundle = _bundle(objects=[
        DetectedObject(object_id="o1", type=ObjectType.POLYBAG, image_id="img1",
                        confidence=0.9, description="No polybag visible around product", detected=False),
    ])
    result = rule_engine.check_polybag(bundle, _rule("polybag"))
    assert result.verdict == Verdict.FAIL


def test_polybag_uncertain_no_evidence():
    bundle = _bundle(objects=[])
    result = rule_engine.check_polybag(bundle, _rule("polybag"))
    assert result.verdict == Verdict.UNCERTAIN
    assert result.resolution_hint is not None


# --------------------------------------------------------------- warning ---
def test_warning_pass_with_ocr():
    bundle = _bundle(objects=[
        DetectedObject(object_id="o1", type=ObjectType.WARNING_LABEL, image_id="img1",
                        bbox=BoundingBox(x1=320, y1=200, x2=620, y2=300), confidence=0.9,
                        description="Warning label block", visibility_state="visible",
                        occlusion_state="none", detected=True),
    ], ocr_results=[
        OCRResult(region_id="r1", image_id="img1", raw_text="SUFFOCATION HAZARD",
                  normalized_text="SUFFOCATION HAZARD", confidence=0.8,
                  bbox=BoundingBox(x1=335, y1=250, x2=600, y2=270)),
    ])
    result = rule_engine.check_warning(bundle, _rule("warning"))
    assert result.verdict == Verdict.PASS


def test_warning_uncertain_unreadable():
    bundle = _bundle(objects=[
        DetectedObject(object_id="o1", type=ObjectType.WARNING_LABEL, image_id="img1",
                        bbox=BoundingBox(x1=320, y1=200, x2=620, y2=300), confidence=0.7,
                        description="Label present but blurry", visibility_state="visible",
                        occlusion_state="none", detected=True),
    ], ocr_results=[])  # no legible OCR text
    result = rule_engine.check_warning(bundle, _rule("warning"))
    assert result.verdict == Verdict.UNCERTAIN


# ----------------------------------------------------------------- fnsku ---
def test_fnsku_fail_on_edge():
    obj = DetectedObject(object_id="o1", type=ObjectType.FNSKU_LABEL, image_id="img1",
                          bbox=BoundingBox(x1=700, y1=400, x2=860, y2=470), confidence=0.9,
                          description="FNSKU near edge", detected=True)
    bundle = _bundle(objects=[obj], geometry=[
        GeometryEvidence(object_id="o1", image_id="img1", surface_type="edge",
                          edge_overlap=True, edge_distance_px=5.0, notes=["near edge"]),
    ])
    result = rule_engine.check_fnsku(bundle, _rule("fnsku"), expected_fnsku="X0DEMO1234")
    assert result.verdict == Verdict.FAIL
    assert "edge" in result.reason.lower() or "seam" in result.reason.lower()


def test_fnsku_pass_flat():
    obj = DetectedObject(object_id="o1", type=ObjectType.FNSKU_LABEL, image_id="img1",
                          bbox=BoundingBox(x1=350, y1=400, x2=590, y2=470), confidence=0.9,
                          description="FNSKU flat on face", detected=True)
    bundle = _bundle(objects=[obj], geometry=[
        GeometryEvidence(object_id="o1", image_id="img1", surface_type="flat",
                          edge_overlap=False, edge_distance_px=300.0, notes=["flat"]),
    ], ocr_results=[
        OCRResult(region_id="r1", image_id="img1", raw_text="X0DEMO1234",
                  normalized_text="X0DEMO1234", confidence=0.8,
                  bbox=BoundingBox(x1=365, y1=410, x2=550, y2=430)),
    ])
    result = rule_engine.check_fnsku(bundle, _rule("fnsku"), expected_fnsku="X0DEMO1234")
    assert result.verdict == Verdict.PASS


def test_fnsku_uncertain_barcode_conflict():
    obj = DetectedObject(object_id="o1", type=ObjectType.FNSKU_LABEL, image_id="img1",
                          bbox=BoundingBox(x1=350, y1=400, x2=590, y2=470), confidence=0.9,
                          description="FNSKU flat", detected=True)
    bundle = _bundle(objects=[obj], geometry=[
        GeometryEvidence(object_id="o1", image_id="img1", surface_type="flat", edge_overlap=False, notes=[]),
    ], barcode_results=[
        BarcodeResult(region_id="b1", image_id="img1", symbology="CODE128", decoded_value="X0WRONG999"),
    ])
    result = rule_engine.check_fnsku(bundle, _rule("fnsku"), expected_fnsku="X0DEMO1234")
    assert result.verdict == Verdict.UNCERTAIN


# --------------------------------------------------------- not-verifiable --
def test_not_visually_verifiable_never_pass_or_fail():
    rule = _rule("not_visually_verifiable", name="Material thickness")
    bundle = _bundle()
    result = rule_engine.check_not_visually_verifiable(bundle, rule)
    assert result.verdict == Verdict.UNCERTAIN


# ------------------------------------------------------------ aggregation --
def test_overall_fail_dominates_uncertain():
    from app.rules.resolver import ResolvedRequirementSet
    bundle = _bundle(objects=[
        DetectedObject(object_id="o1", type=ObjectType.POLYBAG, image_id="img1",
                        confidence=0.9, description="missing", detected=False),
    ])
    resolved = ResolvedRequirementSet(unit_id="u1", requirements=[_rule("polybag")])
    result = rule_engine.evaluate(bundle, resolved, {"fnsku": None})
    assert result.overall_status == Verdict.FAIL


def test_overall_uncertain_when_no_fail_but_uncertain_present():
    from app.rules.resolver import ResolvedRequirementSet
    bundle = _bundle(objects=[])  # no evidence at all -> uncertain
    resolved = ResolvedRequirementSet(unit_id="u1", requirements=[_rule("polybag")])
    result = rule_engine.evaluate(bundle, resolved, {"fnsku": None})
    assert result.overall_status == Verdict.UNCERTAIN


def test_confidence_is_not_directly_thresholded_into_fail():
    """Regression test for spec section 22: low confidence alone must never
    become FAIL — it must become UNCERTAIN unless it clears the absence
    threshold used consistently across the engine."""
    bundle = _bundle(objects=[
        DetectedObject(object_id="o1", type=ObjectType.POLYBAG, image_id="img1",
                        confidence=0.2, description="not sure", detected=False),
    ])
    result = rule_engine.check_polybag(bundle, _rule("polybag"))
    assert result.verdict == Verdict.UNCERTAIN
