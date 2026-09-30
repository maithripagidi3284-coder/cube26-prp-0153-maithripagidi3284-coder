"""
API layer (spec section 34). Authenticated account identity determines the
workspace scope for protected inspection data. Tenant-scoped queries continue
to filter by organization_id, with Postgres row-level security available for
production database enforcement; see docs/postgres_rls.sql.

Authentication uses signed bearer sessions backed by user accounts. Passwords
are hashed with per-user salts, and protected inspection/image/report routes
require an authenticated session.
"""
import time
import uuid
import logging
from datetime import datetime
from pathlib import Path
import mimetypes
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.db import get_db, init_db
from app import models, config, storage, auth
from app.schemas import ReviewSubmission, Verdict, RulebookCreate, SignupRequest, LoginRequest
from app.rules.registry import REQUIREMENTS
from app.services.pipeline import run_analysis, PipelinePendingReview

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prep_manager.api")

app = FastAPI(title="Prep Manager", version=config.SOFTWARE_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_METRICS = {"analyses_run": 0, "total_latency_ms": 0.0, "latencies_ms": []}


@app.on_event("startup")
def _startup():
    # Docker can briefly start the database service before its DNS/accept loop
    # is fully ready. Retry startup DB work without changing application behavior.
    last_error = None
    for attempt in range(15):
        try:
            init_db()
            _migrate_auth_schema()
            _seed_demo_org()
            return
        except OperationalError as exc:
            last_error = exc
            logger.warning("Database not ready (attempt %d/15); retrying...", attempt + 1)
            time.sleep(2)
    raise last_error


def _migrate_auth_schema():
    """Add auth columns when upgrading an existing SQLite/Postgres demo DB."""
    from sqlalchemy import inspect, text
    from app.db import engine, SessionLocal
    inspector = inspect(engine)
    with engine.begin() as conn:
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        additions = {
            "name": "VARCHAR NOT NULL DEFAULT 'Operator'",
            "password_hash": "VARCHAR NOT NULL DEFAULT ''",
            "password_salt": "VARCHAR NOT NULL DEFAULT ''",
        }
        for col, ddl in additions.items():
            if col not in user_cols:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
    db = SessionLocal()
    try:
        for user in db.query(models.User).all():
            if not user.name:
                user.name = "Operator"
        db.commit()
    finally:
        db.close()


def _seed_rulebooks_for_org(db, org_id: str):
    presets = [
        (
            "Standard Polybag + Label",
            "Demo preset covering common photo-verifiable polybag and label checks.",
            ["POLYBAG_PRESENCE", "SUFFOCATION_WARNING", "FNSKU_PLACEMENT",
             "ORIGINAL_BARCODE_COVERAGE", "HANDLING_MARKS", "PACKAGING_MATERIAL_THICKNESS"],
        ),
        (
            "Polybag Only",
            "Demo preset for units that require a polybag but not an FNSKU label.",
            ["POLYBAG_PRESENCE", "SUFFOCATION_WARNING", "PACKAGING_MATERIAL_THICKNESS"],
        ),
        (
            "Label + Barcode",
            "Demo preset focused on fulfillment label placement and original barcode visibility.",
            ["FNSKU_PLACEMENT", "ORIGINAL_BARCODE_COVERAGE"],
        ),
    ]
    for name, description, rule_ids in presets:
        if not db.query(models.Rulebook).filter_by(organization_id=org_id, name=name).first():
            db.add(models.Rulebook(
                organization_id=org_id, name=name, description=description, rule_ids=rule_ids,
            ))


def _seed_demo_org():
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        org = db.query(models.Organization).filter_by(id="demo-org").first()
        if not org:
            db.add(models.Organization(id="demo-org", name="Demo Organization"))
            db.commit()
        _seed_rulebooks_for_org(db, "demo-org")
        db.commit()
    finally:
        db.close()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    user_id = auth.verify_token(authorization[7:].strip())
    if not user_id:
        raise HTTPException(401, "Invalid or expired session")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(401, "Account not found")
    return user.id


def get_current_org(user_id: str = Depends(get_current_user), db: Session = Depends(get_db)) -> str:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(401, "Account not found")
    return user.organization_id


def _scoped(query, model, org_id):
    return query.filter(model.organization_id == org_id)


def _audit(db: Session, org_id: str, unit_id: str, event_type: str, payload: dict):
    db.add(models.AuditEvent(organization_id=org_id, unit_id=unit_id, event_type=event_type, payload=payload))
    db.commit()


# -------------------------------------------------------------- authentication -
@app.post("/auth/signup")
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    email = _normalize_email(payload.email)
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Enter a valid email address")
    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(409, "An account with this email already exists")

    org = models.Organization(id=str(uuid.uuid4()), name=f"{payload.name.strip()}'s Workspace")
    password_hash, password_salt = auth.hash_password(payload.password)
    user = models.User(
        organization_id=org.id,
        email=email,
        name=payload.name.strip(),
        password_hash=password_hash,
        password_salt=password_salt,
        role="operator",
    )
    db.add(org)
    db.add(user)
    db.flush()
    _seed_rulebooks_for_org(db, org.id)
    db.commit()
    return {
        "access_token": auth.create_token(user.id),
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name, "email": user.email},
    }


@app.post("/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = _normalize_email(payload.email)
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not auth.verify_password(payload.password, user.password_hash, user.password_salt):
        raise HTTPException(401, "Incorrect email or password")
    return {
        "access_token": auth.create_token(user.id),
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name, "email": user.email},
    }


@app.get("/auth/me")
def me(user_id: str = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    return {"id": user.id, "name": user.name, "email": user.email}


# ------------------------------------------------------------------ health -
@app.get("/health")
def health():
    return {"status": "ok", "version": config.SOFTWARE_VERSION}


@app.get("/metrics")
def metrics():
    lat = _METRICS["latencies_ms"]
    p50 = sorted(lat)[len(lat) // 2] if lat else None
    p95 = sorted(lat)[int(len(lat) * 0.95)] if lat else None
    return {
        "analyses_run": _METRICS["analyses_run"],
        "avg_latency_ms": (_METRICS["total_latency_ms"] / _METRICS["analyses_run"]) if _METRICS["analyses_run"] else None,
        "p50_latency_ms": p50, "p95_latency_ms": p95,
    }


@app.get("/requirements")
def list_requirements():
    return {"rule_version": "RULES_V1", "requirements": REQUIREMENTS}


@app.get("/rulebooks")
def list_rulebooks(org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    books = (_scoped(db.query(models.Rulebook), models.Rulebook, org_id)
             .order_by(models.Rulebook.created_at.asc()).all())
    return [{
        "rulebook_id": b.id, "name": b.name, "description": b.description or "",
        "rule_ids": b.rule_ids or [], "rule_count": len(b.rule_ids or []),
        "created_at": b.created_at.isoformat(),
    } for b in books]


@app.post("/rulebooks")
def create_rulebook(
    rulebook: RulebookCreate,
    org_id: str = Depends(get_current_org), db: Session = Depends(get_db),
):
    known = {r["rule_id"] for r in REQUIREMENTS}
    unknown = [rid for rid in rulebook.rule_ids if rid not in known]
    if unknown:
        raise HTTPException(400, f"Unknown rule ids: {unknown}")
    if not rulebook.name.strip():
        raise HTTPException(400, "Rulebook name is required")
    item = models.Rulebook(
        organization_id=org_id, name=rulebook.name.strip(),
        description=rulebook.description.strip(),
        rule_ids=list(dict.fromkeys(rulebook.rule_ids)),
    )
    db.add(item)
    db.commit()
    return {
        "rulebook_id": item.id, "name": item.name, "description": item.description or "",
        "rule_ids": item.rule_ids or [], "rule_count": len(item.rule_ids or []),
    }


@app.get("/rulebooks/{rulebook_id}")
def get_rulebook(rulebook_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    item = (_scoped(db.query(models.Rulebook), models.Rulebook, org_id)
            .filter(models.Rulebook.id == rulebook_id).first())
    if not item:
        raise HTTPException(404, "Rulebook not found")
    return {
        "rulebook_id": item.id, "name": item.name, "description": item.description or "",
        "rule_ids": item.rule_ids or [], "rule_count": len(item.rule_ids or []),
        "created_at": item.created_at.isoformat(),
    }


@app.get("/dashboard/summary")
def dashboard_summary(org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    units = (_scoped(db.query(models.Unit), models.Unit, org_id)
             .order_by(models.Unit.created_at.desc()).limit(200).all())
    counts = {"TOTAL": len(units), "PASS": 0, "FAIL": 0, "UNCERTAIN": 0, "PENDING_REVIEW": 0, "NO_RESULT": 0}
    recent = []
    for u in units:
        product = db.query(models.Product).filter_by(id=u.product_id).first()
        latest = (_scoped(db.query(models.ComplianceResult), models.ComplianceResult, org_id)
                  .filter_by(unit_id=u.id).order_by(models.ComplianceResult.created_at.desc()).first())
        status = latest.overall_status if latest else None
        if status in ("PASS", "FAIL", "UNCERTAIN"):
            counts[status] += 1
        elif u.processing_status == "PENDING_REVIEW":
            counts["PENDING_REVIEW"] += 1
        else:
            counts["NO_RESULT"] += 1
        if len(recent) < 8:
            recent.append({
                "unit_id": u.id, "sku": product.sku if product else None,
                "fnsku": product.fnsku if product else None,
                "status": status or u.processing_status, "created_at": u.created_at.isoformat(),
            })
    counts["ANALYZED"] = counts["PASS"] + counts["FAIL"] + counts["UNCERTAIN"]
    return {"counts": counts, "recent": recent}


# ------------------------------------------------------------------- units -
@app.post("/units")
def create_unit(
    sku: str = Form(...), asin: str = Form(""), fnsku: str = Form(""),
    prep_type: str = Form(""), manufacturer_barcode: str = Form(""),
    expiry_required: bool = Form(False), barcode_coverage_required: bool = Form(False),
    handling_marks: str = Form(""),  # comma-separated
    work_order_reference: str = Form(""),
    rulebook_id: str = Form(""),
    org_id: str = Depends(get_current_org), user_id: str = Depends(get_current_user), db: Session = Depends(get_db),
):
    selected_rule_ids = None
    selected_rulebook_name = None
    if rulebook_id:
        rulebook = (_scoped(db.query(models.Rulebook), models.Rulebook, org_id)
                    .filter(models.Rulebook.id == rulebook_id).first())
        if not rulebook:
            raise HTTPException(400, "Selected rulebook was not found")
        selected_rule_ids = list(rulebook.rule_ids or [])
        selected_rulebook_name = rulebook.name

    product = models.Product(
        organization_id=org_id, sku=sku, asin=asin or None, fnsku=fnsku or None,
        category=None, prep_type=prep_type, manufacturer_barcode=manufacturer_barcode or None,
        expected_expiry_required=expiry_required,
        metadata_json={
            "barcode_coverage_required": barcode_coverage_required,
            "handling_marks": [m.strip() for m in handling_marks.split(",") if m.strip()],
            "rulebook_id": rulebook_id or None,
            "rulebook_name": selected_rulebook_name,
            "rule_ids": selected_rule_ids,
        },
    )
    db.add(product)
    db.flush()

    work_order = None
    if work_order_reference:
        work_order = models.WorkOrder(organization_id=org_id, reference=work_order_reference)
        db.add(work_order)
        db.flush()

    unit = models.Unit(
        organization_id=org_id, product_id=product.id,
        work_order_id=work_order.id if work_order else None,
        processing_status="CREATED",
    )
    db.add(unit)
    db.commit()
    _audit(db, org_id, unit.id, "UNIT_CREATED", {"sku": sku})
    return {"unit_id": unit.id, "product_id": product.id}


@app.post("/units/{unit_id}/images")
async def upload_images(
    unit_id: str, files: list[UploadFile] = File(...),
    org_id: str = Depends(get_current_org), db: Session = Depends(get_db),
):
    unit = _scoped(db.query(models.Unit), models.Unit, org_id).filter(models.Unit.id == unit_id).first()
    if not unit:
        raise HTTPException(404, "Unit not found")

    saved = []
    for f in files:
        if f.content_type not in config.ALLOWED_IMAGE_TYPES:
            raise HTTPException(400, f"Unsupported image type: {f.content_type}")
        data = await f.read()
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"Image too large: {f.filename}")

        image_id = str(uuid.uuid4())
        meta = storage.save_original(unit_id, image_id, data, f.content_type)
        image = models.Image(
            id=image_id, organization_id=org_id, unit_id=unit_id, sha256=meta["sha256"],
            storage_path=meta["original_path"], width=meta["width"], height=meta["height"],
            processed_at=meta["processed_at"],
        )
        db.add(image)
        db.flush()
        db.add(models.ImageProcessing(image_id=image_id, blur_score=0, brightness=0, contrast=0, is_usable=True))
        saved.append({"image_id": image_id, "inference_path": meta["inference_path"], "sha256": meta["sha256"]})

    unit.processing_status = "IMAGES_UPLOADED"
    db.commit()
    _audit(db, org_id, unit_id, "IMAGES_UPLOADED", {"count": len(saved)})
    return {"unit_id": unit_id, "images": [s["image_id"] for s in saved]}


@app.post("/units/{unit_id}/analyze")
def analyze_unit(unit_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    unit = _scoped(db.query(models.Unit), models.Unit, org_id).filter(models.Unit.id == unit_id).first()
    if not unit:
        raise HTTPException(404, "Unit not found")
    product = db.query(models.Product).filter_by(id=unit.product_id).first()
    images = db.query(models.Image).filter_by(unit_id=unit_id).all()
    if not images:
        raise HTTPException(400, "No images uploaded for this unit")

    image_payload = []
    for img in images:
        inference_path = img.storage_path.replace("_original", "_inference")
        # inference copy uses .jpg regardless of original ext
        base = img.storage_path.rsplit("_original.", 1)[0]
        image_payload.append({"image_id": img.id, "inference_path": f"{base}_inference.jpg", "sha256": img.sha256})

    product_meta = {
        "prep_type": product.prep_type, "fnsku": product.fnsku,
        "manufacturer_barcode": product.manufacturer_barcode,
        "expiry_required": product.expected_expiry_required,
        "barcode_coverage_required": (product.metadata_json or {}).get("barcode_coverage_required", False),
        "handling_marks": (product.metadata_json or {}).get("handling_marks", []),
        "rule_ids": (product.metadata_json or {}).get("rule_ids"),
    }

    start = time.time()
    unit.processing_status = "ANALYZING"
    db.commit()
    try:
        result, bundle = run_analysis(unit_id, image_payload, product_meta)
    except PipelinePendingReview as exc:
        unit.processing_status = "PENDING_REVIEW"
        db.commit()
        _audit(db, org_id, unit_id, "ANALYSIS_PENDING_REVIEW", {"reason": exc.reason})
        raise HTTPException(202, detail={"status": "PENDING_REVIEW", "reason": exc.reason})

    latency_ms = (time.time() - start) * 1000
    _METRICS["analyses_run"] += 1
    _METRICS["total_latency_ms"] += latency_ms
    _METRICS["latencies_ms"].append(latency_ms)

    db.add(models.VisualEvidence(
        organization_id=org_id, unit_id=unit_id, analysis_id=result.analysis_id,
        model_version=result.model_version, prompt_version=result.prompt_version,
        bundle_json=bundle.model_dump(mode="json"),
    ))
    cr = models.ComplianceResult(
        organization_id=org_id, unit_id=unit_id, analysis_id=result.analysis_id,
        overall_status=result.overall_status.value, rule_version=result.rule_version,
        model_version=result.model_version, prompt_version=result.prompt_version,
        result_json=result.model_dump(mode="json"),
    )
    db.add(cr)
    db.flush()
    for c in result.checks:
        db.add(models.ComplianceCheck(
            compliance_result_id=cr.id, check_id=c.check_id, verdict=c.verdict.value,
            rule_id=c.rule_id, rule_version=c.rule_version, check_json=c.model_dump(mode="json"),
        ))

    unit.processing_status = "ANALYZED" if result.overall_status != Verdict.UNCERTAIN else "PENDING_REVIEW"
    db.commit()
    _audit(db, org_id, unit_id, "ANALYSIS_COMPLETE", {
        "overall_status": result.overall_status.value, "latency_ms": latency_ms,
    })
    return result.model_dump(mode="json")


@app.get("/units")
def list_units(org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    units = (_scoped(db.query(models.Unit), models.Unit, org_id)
             .order_by(models.Unit.created_at.desc()).limit(100).all())
    out = []
    for u in units:
        product = db.query(models.Product).filter_by(id=u.product_id).first()
        latest = (_scoped(db.query(models.ComplianceResult), models.ComplianceResult, org_id)
                  .filter_by(unit_id=u.id).order_by(models.ComplianceResult.created_at.desc()).first())
        metadata = (product.metadata_json or {}) if product else {}
        out.append({
            "unit_id": u.id, "status": u.processing_status,
            "sku": product.sku if product else None,
            "fnsku": product.fnsku if product else None,
            "overall_status": latest.overall_status if latest else None,
            "rulebook_id": metadata.get("rulebook_id"),
            "rulebook_name": metadata.get("rulebook_name"),
            "image_count": db.query(models.Image).filter_by(unit_id=u.id).count(),
            "created_at": u.created_at.isoformat(),
        })
    return out


@app.get("/units/{unit_id}")
def get_unit(unit_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    unit = _scoped(db.query(models.Unit), models.Unit, org_id).filter(models.Unit.id == unit_id).first()
    if not unit:
        raise HTTPException(404, "Unit not found")
    product = db.query(models.Product).filter_by(id=unit.product_id).first()
    images = db.query(models.Image).filter_by(unit_id=unit_id).all()
    metadata = product.metadata_json or {}
    return {
        "unit_id": unit.id, "status": unit.processing_status,
        "product": {
            "sku": product.sku, "asin": product.asin, "fnsku": product.fnsku,
            "prep_type": product.prep_type, "manufacturer_barcode": product.manufacturer_barcode,
            "expiry_required": product.expected_expiry_required,
            "barcode_coverage_required": metadata.get("barcode_coverage_required", False),
            "handling_marks": metadata.get("handling_marks", []),
            "rulebook_id": metadata.get("rulebook_id"),
            "rulebook_name": metadata.get("rulebook_name"),
        },
        "images": [{"image_id": i.id, "width": i.width, "height": i.height,
                    "captured_at": i.captured_at.isoformat() if i.captured_at else None} for i in images],
        "created_at": unit.created_at.isoformat(),
    }


@app.get("/units/{unit_id}/results")
def get_results(unit_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    cr = (_scoped(db.query(models.ComplianceResult), models.ComplianceResult, org_id)
          .filter_by(unit_id=unit_id).order_by(models.ComplianceResult.created_at.desc()).first())
    if not cr:
        raise HTTPException(404, "No analysis results for this unit yet")
    return cr.result_json


@app.get("/units/{unit_id}/evidence")
def get_evidence(unit_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    ve = (_scoped(db.query(models.VisualEvidence), models.VisualEvidence, org_id)
          .filter_by(unit_id=unit_id).order_by(models.VisualEvidence.created_at.desc()).first())
    if not ve:
        raise HTTPException(404, "No evidence recorded for this unit yet")
    return ve.bundle_json


@app.get("/images/{image_id}")
def get_image(image_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    image = (_scoped(db.query(models.Image), models.Image, org_id)
             .filter(models.Image.id == image_id).first())
    if not image:
        raise HTTPException(404, "Image not found")
    original = Path(image.storage_path)
    inference = Path(str(original).rsplit("_original.", 1)[0] + "_inference.jpg")
    path = inference if inference.exists() else original
    if not path.exists():
        raise HTTPException(404, "Stored image file not found")
    return FileResponse(path=str(path), media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg")


@app.get("/units/{unit_id}/report")
def get_report(unit_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    unit = (_scoped(db.query(models.Unit), models.Unit, org_id).filter(models.Unit.id == unit_id).first())
    if not unit:
        raise HTTPException(404, "Unit not found")
    product = db.query(models.Product).filter_by(id=unit.product_id).first()
    result = (_scoped(db.query(models.ComplianceResult), models.ComplianceResult, org_id)
              .filter_by(unit_id=unit_id).order_by(models.ComplianceResult.created_at.desc()).first())
    evidence = (_scoped(db.query(models.VisualEvidence), models.VisualEvidence, org_id)
                .filter_by(unit_id=unit_id).order_by(models.VisualEvidence.created_at.desc()).first())
    audits = (_scoped(db.query(models.AuditEvent), models.AuditEvent, org_id)
              .filter_by(unit_id=unit_id).order_by(models.AuditEvent.created_at).all())
    reviews = []
    if result:
        checks = db.query(models.ComplianceCheck).filter_by(compliance_result_id=result.id).all()
        check_ids = {c.id: c.check_id for c in checks}
        for r in (db.query(models.Review).filter(models.Review.compliance_check_id.in_(list(check_ids))).
                  order_by(models.Review.created_at).all() if check_ids else []):
            reviews.append({
                "check_id": check_ids.get(r.compliance_check_id), "original_verdict": r.original_verdict,
                "reviewer_verdict": r.reviewer_verdict, "review_reason": r.review_reason,
                "reviewer_id": r.reviewer_id, "created_at": r.created_at.isoformat(),
            })
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "unit": {"unit_id": unit.id, "status": unit.processing_status,
                 "created_at": unit.created_at.isoformat()},
        "product": {
            "sku": product.sku, "asin": product.asin, "fnsku": product.fnsku,
            "prep_type": product.prep_type, "manufacturer_barcode": product.manufacturer_barcode,
            **(product.metadata_json or {}),
        },
        "result": result.result_json if result else None,
        "evidence": evidence.bundle_json if evidence else None,
        "reviews": reviews,
        "audit": [{"event_type": a.event_type, "payload": a.payload, "created_at": a.created_at.isoformat()} for a in audits],
    }


@app.post("/units/{unit_id}/review")
def submit_review(
    unit_id: str, review: ReviewSubmission,
    org_id: str = Depends(get_current_org), user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cr = (_scoped(db.query(models.ComplianceResult), models.ComplianceResult, org_id)
          .filter_by(unit_id=unit_id).order_by(models.ComplianceResult.created_at.desc()).first())
    if not cr:
        raise HTTPException(404, "No analysis to review")
    check = db.query(models.ComplianceCheck).filter_by(
        compliance_result_id=cr.id, check_id=review.check_id
    ).first()
    if not check:
        raise HTTPException(404, "Check not found")

    db.add(models.Review(
        organization_id=org_id, compliance_check_id=check.id,
        original_verdict=check.verdict,  # original AI verdict preserved, never overwritten
        reviewer_verdict=review.reviewer_verdict.value,
        review_reason=review.review_reason, reviewer_id=review.reviewer_id or user_id,
    ))
    db.commit()
    _audit(db, org_id, unit_id, "REVIEW_SUBMITTED", {
        "check_id": review.check_id, "original": check.verdict,
        "reviewer_verdict": review.reviewer_verdict.value,
    })
    return {"status": "recorded", "original_verdict": check.verdict, "reviewer_verdict": review.reviewer_verdict.value}


@app.get("/units/{unit_id}/audit")
def get_audit(unit_id: str, org_id: str = Depends(get_current_org), db: Session = Depends(get_db)):
    events = (_scoped(db.query(models.AuditEvent), models.AuditEvent, org_id)
              .filter_by(unit_id=unit_id).order_by(models.AuditEvent.created_at).all())
    return [{"event_type": e.event_type, "payload": e.payload, "created_at": e.created_at.isoformat()} for e in events]
