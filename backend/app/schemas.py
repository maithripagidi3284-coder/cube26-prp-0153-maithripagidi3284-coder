"""
Pydantic schemas = the decision/evidence contracts (spec sections 24, 65, 66).
These are the shapes every layer must produce/consume. Nothing downstream is
allowed to invent fields that aren't here.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class VisualVerifiability(str, Enum):
    VISUAL = "VISUAL"
    PARTIALLY_VISUAL = "PARTIALLY_VISUAL"
    NOT_VISUALLY_VERIFIABLE = "NOT_VISUALLY_VERIFIABLE"


class ObjectType(str, Enum):
    PRODUCT = "PRODUCT"
    POLYBAG = "POLYBAG"
    POLYBAG_OPENING = "POLYBAG_OPENING"
    POLYBAG_SEAL = "POLYBAG_SEAL"
    WARNING_LABEL = "WARNING_LABEL"
    FNSKU_LABEL = "FNSKU_LABEL"
    MANUFACTURER_BARCODE = "MANUFACTURER_BARCODE"
    EXPIRY_DATE = "EXPIRY_DATE"
    HANDLING_MARK = "HANDLING_MARK"
    SEAM = "SEAM"
    EDGE = "EDGE"
    CORNER = "CORNER"
    CURVED_SURFACE = "CURVED_SURFACE"
    FLAT_SURFACE = "FLAT_SURFACE"


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class DetectedObject(BaseModel):
    """One region the VLM (or CV fallback) claims to see. Never fabricated —
    if the model isn't sure, detected=False / status is uncertain upstream,
    it does not just omit the field silently."""
    object_id: str
    type: ObjectType
    image_id: str
    bbox: Optional[BoundingBox] = None
    confidence: float = Field(ge=0.0, le=1.0)
    description: str = ""
    ocr_text: Optional[str] = None
    visibility_state: str = "unknown"  # visible | partially_occluded | occluded | unknown
    occlusion_state: str = "unknown"
    spatial_relationships: List[str] = Field(default_factory=list)
    detected: bool = True


class OCRResult(BaseModel):
    region_id: str
    image_id: str
    raw_text: str
    normalized_text: str
    confidence: float
    bbox: Optional[BoundingBox] = None
    source: str = "ocr_engine"  # ocr_engine | vlm


class BarcodeResult(BaseModel):
    region_id: str
    image_id: str
    symbology: str
    decoded_value: str
    bbox: Optional[BoundingBox] = None
    confidence: float = 1.0  # zbar decodes are binary-correct or absent


class ImageQuality(BaseModel):
    image_id: str
    width: int
    height: int
    sha256: str
    blur_score: float
    brightness: float
    contrast: float
    is_usable: bool
    quality_notes: List[str] = Field(default_factory=list)


class GeometryEvidence(BaseModel):
    """Output of the OpenCV geometry engine for one detected label/region."""
    object_id: str
    image_id: str
    surface_type: str = "unknown"  # flat | curved | edge | seam | unknown
    seam_overlap_ratio: Optional[float] = None
    edge_overlap: Optional[bool] = None
    edge_distance_px: Optional[float] = None
    orientation_deg: Optional[float] = None
    notes: List[str] = Field(default_factory=list)


class VisualEvidenceBundle(BaseModel):
    """The full structured output of the single per-unit VLM call
    (spec section 30) plus locally-computed CV/OCR/barcode/quality evidence
    merged in. This is the ONLY input the rule engine is allowed to see."""
    unit_id: str
    analysis_id: str
    prompt_version: str
    model_version: str
    image_ids: List[str]
    image_quality: List[ImageQuality] = Field(default_factory=list)
    objects: List[DetectedObject] = Field(default_factory=list)
    ocr_results: List[OCRResult] = Field(default_factory=list)
    barcode_results: List[BarcodeResult] = Field(default_factory=list)
    geometry: List[GeometryEvidence] = Field(default_factory=list)
    raw_vlm_response: Optional[Dict[str, Any]] = None


class EvidenceItem(BaseModel):
    image_id: str
    bbox: Optional[BoundingBox] = None
    description: str
    source: str  # vlm | ocr | barcode | geometry | quality


class ComplianceCheckResult(BaseModel):
    """Spec section 65 - final decision contract, per check."""
    check_id: str
    requirement_name: str
    verdict: Verdict
    reason: str
    evidence: List[EvidenceItem] = Field(default_factory=list)
    rule_id: str
    rule_version: str
    source: str
    visual_evidence_sufficient: bool
    review_required: bool = False
    resolution_hint: Optional[str] = None  # "what would resolve UNCERTAIN"


class OverallResult(BaseModel):
    """Spec section 66 - overall decision contract."""
    unit_id: str
    overall_status: Verdict
    checks: List[ComplianceCheckResult]
    failed_checks: List[str]
    uncertain_checks: List[str]
    evidence_count: int
    rule_version: str
    model_version: str
    prompt_version: str
    analysis_id: str
    timestamp: datetime


class RulebookCreate(BaseModel):
    name: str
    description: str = ""
    rule_ids: List[str] = Field(default_factory=list)


class ReviewSubmission(BaseModel):
    check_id: str
    reviewer_verdict: Verdict
    review_reason: str
    reviewer_id: str


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=1, max_length=128)
