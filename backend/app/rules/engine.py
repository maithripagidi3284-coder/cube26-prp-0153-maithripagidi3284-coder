"""
Deterministic compliance rule engine (spec sections 21, 22, 23, 65, 66).

THIS is the only place a PASS/FAIL/UNCERTAIN verdict is decided. It consumes
the VisualEvidenceBundle (VLM objects + OCR + barcode + geometry + image
quality) and a ResolvedRequirementSet, and never calls the VLM itself.

Rule of thumb enforced throughout:
  evidence proves compliance   -> PASS
  evidence proves violation    -> FAIL
  evidence is insufficient     -> UNCERTAIN
Confidence scores are never thresholded directly into FAIL (spec section 22)
— they only ever gate whether evidence counts as "proving" something.
"""
import uuid
from datetime import datetime

from app.schemas import (
    Verdict, ObjectType, ComplianceCheckResult, EvidenceItem,
    OverallResult, VisualEvidenceBundle,
)
from app.rules.resolver import ResolvedRequirementSet, ResolvedRequirement
from app.ocr.engine import normalize_identifier
from app import config

# Evidence-strength thresholds (engineering heuristics, isolated from rule
# *logic* itself — changing these does not change what a rule means).
STRONG_DETECTION_CONF = 0.65
STRONG_ABSENCE_CONF = 0.65
OCR_MATCH_MIN_CONF = 0.35


def _objects_of_type(bundle: VisualEvidenceBundle, t: ObjectType):
    return [o for o in bundle.objects if o.type == t]


def _image_usable(bundle: VisualEvidenceBundle, image_id: str) -> bool:
    for q in bundle.image_quality:
        if q.image_id == image_id:
            return q.is_usable
    return True  # no quality record => don't block on it


def _geometry_for(bundle: VisualEvidenceBundle, object_id: str):
    for g in bundle.geometry:
        if g.object_id == object_id:
            return g
    return None


def _evidence_from_object(o, note: str = None) -> EvidenceItem:
    return EvidenceItem(
        image_id=o.image_id, bbox=o.bbox,
        description=note or o.description, source="vlm",
    )


def _uncertain(check_id, name, rule: ResolvedRequirement, reason, evidence, hint) -> ComplianceCheckResult:
    return ComplianceCheckResult(
        check_id=check_id, requirement_name=name, verdict=Verdict.UNCERTAIN,
        reason=reason, evidence=evidence, rule_id=rule.rule_id,
        rule_version=rule.version, source=rule.source_name,
        visual_evidence_sufficient=False, review_required=True,
        resolution_hint=hint,
    )


# ---------------------------------------------------------------- POLYBAG --
def check_polybag(bundle: VisualEvidenceBundle, rule: ResolvedRequirement) -> ComplianceCheckResult:
    check_id = "POLYBAG_PRESENCE"
    bags = [o for o in _objects_of_type(bundle, ObjectType.POLYBAG) if o.detected]
    absence = [o for o in _objects_of_type(bundle, ObjectType.POLYBAG) if not o.detected]

    if not bags and absence:
        best_absence = max(absence, key=lambda o: o.confidence)
        if best_absence.confidence >= STRONG_ABSENCE_CONF and _image_usable(bundle, best_absence.image_id):
            return ComplianceCheckResult(
                check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
                reason="No polybag was detected enclosing the product.",
                evidence=[_evidence_from_object(best_absence)], rule_id=rule.rule_id,
                rule_version=rule.version, source=rule.source_name,
                visual_evidence_sufficient=True,
            )
        return _uncertain(check_id, rule.name, rule,
                           "Polybag was not clearly detected, but evidence is not strong enough to confirm absence.",
                           [_evidence_from_object(best_absence)],
                           "Capture a wider photo showing the full product and any surrounding packaging.")

    if not bags:
        return _uncertain(check_id, rule.name, rule,
                           "No polybag evidence found in the supplied images.",
                           [], "Capture a photo clearly showing whether the product is bagged.")

    best_bag = max(bags, key=lambda o: o.confidence)
    if not _image_usable(bundle, best_bag.image_id):
        return _uncertain(check_id, rule.name, rule,
                           "A possible polybag was detected but the image quality is too poor to confirm sealing.",
                           [_evidence_from_object(best_bag)],
                           "Retake the photo with better focus/lighting.")

    seals = [o for o in _objects_of_type(bundle, ObjectType.POLYBAG_SEAL) if o.detected]
    openings = [o for o in _objects_of_type(bundle, ObjectType.POLYBAG_OPENING) if o.detected]

    if seals and best_bag.confidence >= STRONG_DETECTION_CONF:
        seal = max(seals, key=lambda o: o.confidence)
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.PASS,
            reason="A sealed polybag enclosing the product was detected.",
            evidence=[_evidence_from_object(best_bag), _evidence_from_object(seal, "Seal region")],
            rule_id=rule.rule_id, rule_version=rule.version, source=rule.source_name,
            visual_evidence_sufficient=True,
        )

    if openings:
        opening = max(openings, key=lambda o: o.confidence)
        if opening.confidence >= STRONG_DETECTION_CONF and opening.occlusion_state != "full":
            return ComplianceCheckResult(
                check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
                reason="Polybag is present but an open/unsealed opening is visible.",
                evidence=[_evidence_from_object(best_bag), _evidence_from_object(opening, "Unsealed opening")],
                rule_id=rule.rule_id, rule_version=rule.version, source=rule.source_name,
                visual_evidence_sufficient=True,
            )

    return _uncertain(check_id, rule.name, rule,
                       "Polybag is present but the seal/opening region is not clearly visible.",
                       [_evidence_from_object(best_bag)],
                       "Capture a close-up photo of the polybag's seal or opening.")


# ------------------------------------------------------------ WARNING -----
def check_warning(bundle: VisualEvidenceBundle, rule: ResolvedRequirement) -> ComplianceCheckResult:
    check_id = "SUFFOCATION_WARNING"
    labels = [o for o in _objects_of_type(bundle, ObjectType.WARNING_LABEL) if o.detected]
    absence = [o for o in _objects_of_type(bundle, ObjectType.WARNING_LABEL) if not o.detected]

    if not labels:
        if absence:
            best_absence = max(absence, key=lambda o: o.confidence)
            if best_absence.confidence >= STRONG_ABSENCE_CONF and _image_usable(bundle, best_absence.image_id):
                return ComplianceCheckResult(
                    check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
                    reason="No suffocation warning label was found despite it being required.",
                    evidence=[_evidence_from_object(best_absence)], rule_id=rule.rule_id,
                    rule_version=rule.version, source=rule.source_name, visual_evidence_sufficient=True,
                )
        return _uncertain(check_id, rule.name, rule,
                           "Warning-label region was not clearly identified.",
                           [], "Capture a photo showing the full polybag surface where a warning label would be.")

    best = max(labels, key=lambda o: o.confidence)
    if not _image_usable(bundle, best.image_id) or best.occlusion_state in ("full",):
        return _uncertain(check_id, rule.name, rule,
                           "Warning label region detected but occluded/unreadable image quality prevents confirming legibility.",
                           [_evidence_from_object(best)],
                           "Capture a closer, well-lit photo of the warning label.")

    matching_ocr = [t for t in bundle.ocr_results
                     if t.image_id == best.image_id and t.confidence >= OCR_MATCH_MIN_CONF
                     and ("suffocat" in t.normalized_text.lower() or "warning" in t.normalized_text.lower()
                          or "plastic" in t.normalized_text.lower() or "children" in t.normalized_text.lower())]

    if matching_ocr and best.confidence >= STRONG_DETECTION_CONF:
        ev = [_evidence_from_object(best)] + [
            EvidenceItem(image_id=t.image_id, bbox=t.bbox, description=f'OCR read: "{t.normalized_text}"', source="ocr")
            for t in matching_ocr[:2]
        ]
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.PASS,
            reason="Suffocation warning label is present, visible, and OCR confirms legible warning text.",
            evidence=ev, rule_id=rule.rule_id, rule_version=rule.version,
            source=rule.source_name, visual_evidence_sufficient=True,
        )

    return _uncertain(check_id, rule.name, rule,
                       "A warning-label-shaped region is visible, but its text could not be reliably read (OCR did not confirm warning language).",
                       [_evidence_from_object(best)],
                       "Capture a sharper, closer photo of the warning label text.")


# ------------------------------------------------------------- FNSKU ------
def check_fnsku(bundle: VisualEvidenceBundle, rule: ResolvedRequirement, expected_fnsku: str | None) -> ComplianceCheckResult:
    check_id = "FNSKU_PLACEMENT"
    labels = [o for o in _objects_of_type(bundle, ObjectType.FNSKU_LABEL) if o.detected]
    absence = [o for o in _objects_of_type(bundle, ObjectType.FNSKU_LABEL) if not o.detected]

    if not labels:
        if absence:
            best_absence = max(absence, key=lambda o: o.confidence)
            if best_absence.confidence >= STRONG_ABSENCE_CONF and _image_usable(bundle, best_absence.image_id):
                return ComplianceCheckResult(
                    check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
                    reason="No FNSKU label was found on the unit.",
                    evidence=[_evidence_from_object(best_absence)], rule_id=rule.rule_id,
                    rule_version=rule.version, source=rule.source_name, visual_evidence_sufficient=True,
                )
        return _uncertain(check_id, rule.name, rule, "FNSKU label was not clearly identified in the supplied images.",
                           [], "Capture a photo clearly showing the FNSKU label location.")

    best = max(labels, key=lambda o: o.confidence)
    evidence = [_evidence_from_object(best)]

    if not _image_usable(bundle, best.image_id):
        return _uncertain(check_id, rule.name, rule,
                           "FNSKU label detected but image quality is too poor for placement/legibility analysis.",
                           evidence, "Retake the photo with better focus and lighting, closer to the label.")

    # Identifier match: barcode decode is authoritative when available.
    barcode_match = None
    for b in bundle.barcode_results:
        if b.image_id != best.image_id:
            continue
        if expected_fnsku and normalize_identifier(b.decoded_value) == normalize_identifier(expected_fnsku):
            barcode_match = True
        elif expected_fnsku:
            barcode_match = False

    ocr_texts = [t for t in bundle.ocr_results if t.image_id == best.image_id and t.confidence >= OCR_MATCH_MIN_CONF]
    ocr_match = None
    if expected_fnsku:
        for t in ocr_texts:
            if normalize_identifier(expected_fnsku) in normalize_identifier(t.raw_text):
                ocr_match = True
                break

    if expected_fnsku and barcode_match is False:
        return _uncertain(check_id, rule.name, rule,
                           f"Decoded barcode does not match the expected FNSKU ({expected_fnsku}); conflicting evidence must not be auto-resolved.",
                           evidence, "Manually verify the FNSKU against the work order.")

    # Geometry: seam/edge placement.
    geo = _geometry_for(bundle, best.object_id)
    if geo and geo.surface_type in ("seam", "edge") and best.confidence >= STRONG_DETECTION_CONF:
        loc = "a package seam" if geo.surface_type == "seam" else "the package edge"
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
            reason=f"FNSKU label placement overlaps {loc}, which prevents reliable scanning.",
            evidence=evidence + [EvidenceItem(image_id=geo.image_id, bbox=best.bbox,
                                               description=f"Geometry: {', '.join(geo.notes)}", source="geometry")],
            rule_id=rule.rule_id, rule_version=rule.version, source=rule.source_name,
            visual_evidence_sufficient=True,
        )
    if geo is None or geo.surface_type == "unknown":
        return _uncertain(check_id, rule.name, rule,
                           "FNSKU label detected but its placement relative to seams/edges could not be reliably determined.",
                           evidence + ([EvidenceItem(image_id=geo.image_id, bbox=best.bbox,
                                        description=f"Geometry: {', '.join(geo.notes)}", source="geometry")] if geo else []),
                           "Capture a straight-on photo showing the label and surrounding package surface.")

    # Placement looks flat and clean. Confirm legibility/identifier match.
    if (barcode_match or ocr_match or not expected_fnsku) and best.confidence >= STRONG_DETECTION_CONF:
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.PASS,
            reason="FNSKU label is present, flat, off seam/edge, and legible.",
            evidence=evidence, rule_id=rule.rule_id, rule_version=rule.version,
            source=rule.source_name, visual_evidence_sufficient=True,
        )

    return _uncertain(check_id, rule.name, rule,
                       "FNSKU label placement looks acceptable but the code could not be confidently read/matched.",
                       evidence, "Capture a closer, well-lit photo directly facing the FNSKU label.")


# ------------------------------------------------------- BARCODE COVERAGE -
def check_barcode_coverage(bundle: VisualEvidenceBundle, rule: ResolvedRequirement) -> ComplianceCheckResult:
    check_id = "ORIGINAL_BARCODE_COVERAGE"
    barcodes = [o for o in _objects_of_type(bundle, ObjectType.MANUFACTURER_BARCODE) if o.detected]
    absence = [o for o in _objects_of_type(bundle, ObjectType.MANUFACTURER_BARCODE) if not o.detected]

    decoded_original = [b for b in bundle.barcode_results]  # zbar decode = visible & scannable, deterministic

    if decoded_original:
        b = decoded_original[0]
        obj_match = next((o for o in barcodes if o.image_id == b.image_id), None)
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
            reason="Original manufacturer barcode is still scannable/decodable — it is not covered.",
            evidence=[EvidenceItem(image_id=b.image_id, bbox=(obj_match.bbox if obj_match else b.bbox),
                                    description=f"Decoded original barcode: {b.decoded_value}", source="barcode")],
            rule_id=rule.rule_id, rule_version=rule.version, source=rule.source_name,
            visual_evidence_sufficient=True,
        )

    if not barcodes and not absence:
        return _uncertain(check_id, rule.name, rule,
                           "No original barcode region was identified in the images.",
                           [], "Capture a photo of the area where the original manufacturer barcode is located.")

    if absence:
        best_absence = max(absence, key=lambda o: o.confidence)
        if best_absence.confidence >= STRONG_ABSENCE_CONF and _image_usable(bundle, best_absence.image_id):
            return ComplianceCheckResult(
                check_id=check_id, requirement_name=rule.name, verdict=Verdict.PASS,
                reason="Original manufacturer barcode is not visible/decodable — appears covered.",
                evidence=[_evidence_from_object(best_absence)], rule_id=rule.rule_id,
                rule_version=rule.version, source=rule.source_name, visual_evidence_sufficient=True,
            )

    best = max(barcodes, key=lambda o: o.confidence) if barcodes else None
    if best and not _image_usable(bundle, best.image_id):
        return _uncertain(check_id, rule.name, rule,
                           "Barcode region visible but image quality is too poor to confirm whether it is scannable.",
                           [_evidence_from_object(best)], "Retake the photo with better lighting/focus.")

    return _uncertain(check_id, rule.name, rule,
                       "Barcode coverage state could not be reliably determined from available evidence.",
                       [_evidence_from_object(best)] if best else [],
                       "Capture a clear, well-lit photo of the original barcode area.")


# ----------------------------------------------------------- EXPIRY DATE --
def check_expiry(bundle: VisualEvidenceBundle, rule: ResolvedRequirement) -> ComplianceCheckResult:
    check_id = "EXPIRY_DATE_VISIBILITY"
    dates = [o for o in _objects_of_type(bundle, ObjectType.EXPIRY_DATE) if o.detected]
    absence = [o for o in _objects_of_type(bundle, ObjectType.EXPIRY_DATE) if not o.detected]

    if not dates:
        if absence:
            best_absence = max(absence, key=lambda o: o.confidence)
            if best_absence.confidence >= STRONG_ABSENCE_CONF and _image_usable(bundle, best_absence.image_id):
                return ComplianceCheckResult(
                    check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
                    reason="Expiry date is required but not visible anywhere on the unit.",
                    evidence=[_evidence_from_object(best_absence)], rule_id=rule.rule_id,
                    rule_version=rule.version, source=rule.source_name, visual_evidence_sufficient=True,
                )
        return _uncertain(check_id, rule.name, rule, "Expiry-date region was not identified in the images.",
                           [], "Capture a photo of the area where the expiry date is printed.")

    best = max(dates, key=lambda o: o.confidence)
    evidence = [_evidence_from_object(best)]
    if not _image_usable(bundle, best.image_id):
        return _uncertain(check_id, rule.name, rule,
                           "Expiry-date region detected, but image quality prevents reliable reading.",
                           evidence, "Capture a closer, well-lit, in-focus photo of the expiry date.")

    ocr_hits = [t for t in bundle.ocr_results if t.image_id == best.image_id and t.confidence >= OCR_MATCH_MIN_CONF]
    date_like = [t for t in ocr_hits if any(c.isdigit() for c in t.raw_text)]

    if best.occlusion_state == "full":
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
            reason="Expiry date region is fully occluded after prep.",
            evidence=evidence, rule_id=rule.rule_id, rule_version=rule.version,
            source=rule.source_name, visual_evidence_sufficient=True,
        )

    if date_like and best.confidence >= STRONG_DETECTION_CONF:
        t = max(date_like, key=lambda t: t.confidence)
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.PASS,
            reason="Expiry date is visible and legible.",
            evidence=evidence + [EvidenceItem(image_id=t.image_id, bbox=t.bbox,
                                               description=f'OCR read: "{t.normalized_text}"', source="ocr")],
            rule_id=rule.rule_id, rule_version=rule.version, source=rule.source_name,
            visual_evidence_sufficient=True,
        )

    return _uncertain(check_id, rule.name, rule,
                       "Expiry-date region detected but the characters could not be reliably read by OCR.",
                       evidence, "Capture a sharper close-up photo directly facing the expiry date.")


# ------------------------------------------------------- HANDLING MARKS ---
def check_handling_marks(bundle: VisualEvidenceBundle, rule: ResolvedRequirement, required_marks: list[str]) -> ComplianceCheckResult:
    check_id = "HANDLING_MARKS"
    marks = [o for o in _objects_of_type(bundle, ObjectType.HANDLING_MARK) if o.detected]
    found_text = " ".join([m.description.lower() for m in marks] +
                           [t.normalized_text.lower() for t in bundle.ocr_results])

    missing = [m for m in required_marks if m.lower() not in found_text]
    if not marks and not bundle.ocr_results:
        return _uncertain(check_id, rule.name, rule,
                           "No handling-mark evidence available in the supplied images.",
                           [], "Capture a photo showing all sides of the package with printed handling marks.")

    if not missing:
        ev = [_evidence_from_object(m) for m in marks[:3]]
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.PASS,
            reason=f"All required handling marks were found: {', '.join(required_marks)}.",
            evidence=ev, rule_id=rule.rule_id, rule_version=rule.version,
            source=rule.source_name, visual_evidence_sufficient=True,
        )

    # Only FAIL if we have reasonably good image quality across images (i.e.
    # a genuine search was possible), else UNCERTAIN.
    usable_images = any(q.is_usable for q in bundle.image_quality) or not bundle.image_quality
    if usable_images:
        return ComplianceCheckResult(
            check_id=check_id, requirement_name=rule.name, verdict=Verdict.FAIL,
            reason=f"Required handling mark(s) not found: {', '.join(missing)}.",
            evidence=[_evidence_from_object(m) for m in marks[:3]],
            rule_id=rule.rule_id, rule_version=rule.version, source=rule.source_name,
            visual_evidence_sufficient=True,
        )
    return _uncertain(check_id, rule.name, rule,
                       f"Could not confirm presence of: {', '.join(missing)} — image quality insufficient.",
                       [], "Capture clearer photos of all package sides.")


# --------------------------------------------------- NOT VISUALLY VERIFIABLE
def check_not_visually_verifiable(bundle: VisualEvidenceBundle, rule: ResolvedRequirement) -> ComplianceCheckResult:
    return ComplianceCheckResult(
        check_id=rule.rule_id, requirement_name=rule.name, verdict=Verdict.UNCERTAIN,
        reason="This requirement cannot be reliably established from the supplied photographs.",
        evidence=[], rule_id=rule.rule_id, rule_version=rule.version, source=rule.source_name,
        visual_evidence_sufficient=False, review_required=False,
        resolution_hint="This property requires physical inspection, not photographic evidence.",
    )


_CHECK_DISPATCH = {
    "polybag": lambda bundle, rule, ctx: check_polybag(bundle, rule),
    "warning": lambda bundle, rule, ctx: check_warning(bundle, rule),
    "fnsku": lambda bundle, rule, ctx: check_fnsku(bundle, rule, ctx.get("expected_fnsku")),
    "barcode_coverage": lambda bundle, rule, ctx: check_barcode_coverage(bundle, rule),
    "expiry": lambda bundle, rule, ctx: check_expiry(bundle, rule),
    "handling_marks": lambda bundle, rule, ctx: check_handling_marks(bundle, rule, ctx.get("required_marks", [])),
    "not_visually_verifiable": lambda bundle, rule, ctx: check_not_visually_verifiable(bundle, rule),
}


def evaluate(bundle: VisualEvidenceBundle, resolved: ResolvedRequirementSet, product_meta: dict) -> OverallResult:
    ctx = {"expected_fnsku": product_meta.get("fnsku"), "required_marks": resolved.handling_marks}
    checks: list[ComplianceCheckResult] = []

    for req in resolved.requirements:
        if req.visual_verifiability == "NOT_VISUALLY_VERIFIABLE":
            checks.append(check_not_visually_verifiable(bundle, req))
            continue
        fn = _CHECK_DISPATCH.get(req.check)
        if fn is None:
            checks.append(_uncertain(req.rule_id, req.name, req,
                                      "No evaluator implemented for this check.", [], None))
            continue
        checks.append(fn(bundle, req, ctx))

    failed = [c.check_id for c in checks if c.verdict == Verdict.FAIL]
    uncertain = [c.check_id for c in checks if c.verdict == Verdict.UNCERTAIN]

    # Deterministic aggregation (spec section 23) — LLM never decides this.
    if failed:
        overall = Verdict.FAIL
    elif uncertain:
        overall = Verdict.UNCERTAIN
    else:
        overall = Verdict.PASS

    return OverallResult(
        unit_id=bundle.unit_id, overall_status=overall, checks=checks,
        failed_checks=failed, uncertain_checks=uncertain,
        evidence_count=sum(len(c.evidence) for c in checks),
        rule_version=config.RULE_ENGINE_VERSION, model_version=bundle.model_version,
        prompt_version=bundle.prompt_version, analysis_id=bundle.analysis_id,
        timestamp=datetime.utcnow(),
    )
