"""
Database schema (spec section 31). Dev/demo runs on SQLite (see config.py);
every tenant-scoped table carries organization_id so the same models map
directly onto Postgres with row-level security enabled — see
docs/postgres_rls.sql for the production RLS policies SQLite can't enforce.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, JSON, Text
)
from sqlalchemy.orm import relationship
from app.db import Base


def uid() -> str:
    return str(uuid.uuid4())


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(String, primary_key=True, default=uid)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String, nullable=False)
    role = Column(String, default="operator")  # operator | reviewer | admin
    created_at = Column(DateTime, default=datetime.utcnow)


class Product(Base):
    __tablename__ = "products"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    sku = Column(String, index=True)
    asin = Column(String, index=True)
    fnsku = Column(String, index=True)
    category = Column(String)
    prep_type = Column(String)
    manufacturer_barcode = Column(String)
    expected_expiry_required = Column(Boolean, default=False)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    reference = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class Unit(Base):
    __tablename__ = "units"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    work_order_id = Column(String, ForeignKey("work_orders.id"), nullable=True)
    processing_status = Column(String, default="CREATED")
    # CREATED | IMAGES_UPLOADED | ANALYZING | ANALYZED | PENDING_REVIEW | ERROR
    created_at = Column(DateTime, default=datetime.utcnow)

    images = relationship("Image", back_populates="unit")


class Rulebook(Base):
    """Reusable inspection preset selected by operators before analysis.

    rule_ids contains requirement registry IDs such as POLYBAG_PRESENCE and
    FNSKU_PLACEMENT. The registry remains the source of rule definitions; a
    rulebook only selects which registered rules are applied to a unit.
    """
    __tablename__ = "rulebooks"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    rule_ids = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RequirementVersion(Base):
    """Spec section 47 — rules are never edited in place, only versioned."""
    __tablename__ = "requirement_versions"
    id = Column(String, primary_key=True, default=uid)
    rule_id = Column(String, nullable=False, index=True)
    version = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    applicability = Column(JSON, default=dict)  # e.g. {"category": "electronics"}
    requirement_logic = Column(JSON, default=dict)  # structured, engine-readable
    visual_verifiability = Column(String, default="VISUAL")
    source_name = Column(String)
    source_url = Column(String)
    source_version = Column(String)
    effective_date = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class Image(Base):
    __tablename__ = "images"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    unit_id = Column(String, ForeignKey("units.id"), nullable=False, index=True)
    sha256 = Column(String, index=True)
    storage_path = Column(String, nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    captured_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    unit = relationship("Unit", back_populates="images")


class ImageProcessing(Base):
    __tablename__ = "image_processing"
    id = Column(String, primary_key=True, default=uid)
    image_id = Column(String, ForeignKey("images.id"), nullable=False, index=True)
    blur_score = Column(Float)
    brightness = Column(Float)
    contrast = Column(Float)
    is_usable = Column(Boolean)
    quality_notes = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class VisualEvidence(Base):
    """Stores the full merged evidence bundle (VLM + OCR + barcode + geometry)
    for one analysis run, exactly as fed to the rule engine — for audit."""
    __tablename__ = "visual_evidence"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    unit_id = Column(String, ForeignKey("units.id"), nullable=False, index=True)
    analysis_id = Column(String, nullable=False, index=True)
    model_version = Column(String)
    prompt_version = Column(String)
    bundle_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ComplianceResult(Base):
    __tablename__ = "compliance_results"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    unit_id = Column(String, ForeignKey("units.id"), nullable=False, index=True)
    analysis_id = Column(String, nullable=False, index=True)
    overall_status = Column(String, nullable=False)
    rule_version = Column(String)
    model_version = Column(String)
    prompt_version = Column(String)
    result_json = Column(JSON, nullable=False)  # full OverallResult payload
    created_at = Column(DateTime, default=datetime.utcnow)


class ComplianceCheck(Base):
    __tablename__ = "compliance_checks"
    id = Column(String, primary_key=True, default=uid)
    compliance_result_id = Column(String, ForeignKey("compliance_results.id"), nullable=False, index=True)
    check_id = Column(String, nullable=False)
    verdict = Column(String, nullable=False)
    rule_id = Column(String)
    rule_version = Column(String)
    check_json = Column(JSON, nullable=False)  # full ComplianceCheckResult
    created_at = Column(DateTime, default=datetime.utcnow)


class Review(Base):
    """Human review overrides. Spec section 27 — overrides are data, the
    original AI verdict is never deleted."""
    __tablename__ = "reviews"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    compliance_check_id = Column(String, ForeignKey("compliance_checks.id"), nullable=False, index=True)
    original_verdict = Column(String, nullable=False)
    reviewer_verdict = Column(String, nullable=False)
    review_reason = Column(Text)
    reviewer_id = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(String, primary_key=True, default=uid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    unit_id = Column(String, index=True)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
