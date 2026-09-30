# Prep Manager — Architecture

## 1. Core principle

Prep Manager is not "AI says PASS or FAIL." It is:

> AI extracts visual evidence → authoritative rules define what must be
> true → deterministic reasoning evaluates that evidence → every decision
> is traceable → insufficient evidence becomes UNCERTAIN.

Every architectural decision below exists to keep those five steps
separate and auditable.

## 2. System diagram

```
                    ┌────────────────────┐
  Operator photo →  │  Image ingestion    │  hash, EXIF-normalize, never
                    │  (storage.py)        │  overwrite the original
                    └─────────┬────────────┘
                              │
                    ┌─────────▼────────────┐
                    │  Quality gate         │  blur / brightness / resolution
                    │  (quality/engine.py)  │  — fails open to UNCERTAIN
                    └─────────┬────────────┘
                              │
                    ┌─────────▼────────────┐
     ONE call  ---> │  Vision evidence      │  Groq multimodal vision API, structured
                    │  extraction           │  JSON only, NEVER outputs a
                    │  (vision/client.py)   │  verdict — objects only
                    └─────────┬────────────┘
                              │  DetectedObject[]
              ┌───────────────┼────────────────┐
              ▼                ▼                ▼
      ┌───────────────┐ ┌─────────────┐ ┌───────────────┐
      │ OCR            │ │ Barcode     │ │ Geometry       │
      │ (ocr/engine.py)│ │ decode      │ │ (edge/seam)    │
      │ Tesseract      │ │ (zbar)      │ │ OpenCV         │
      └───────┬────────┘ └──────┬──────┘ └───────┬────────┘
              └─────────────────┴────────────────┘
                              │  merged into
                    ┌─────────▼────────────┐
                    │ VisualEvidenceBundle  │  (schemas.py) — the ONLY input
                    └─────────┬────────────┘  the rule engine may read
                              │
      ┌───────────────────────┼────────────────────────┐
      │  Requirement resolver  (rules/resolver.py)      │
      │  product metadata → ResolvedRequirementSet,      │
      │  each item traced to a versioned rule             │
      └───────────────────────┬────────────────────────┘
                              │
                    ┌─────────▼────────────┐
                    │ Deterministic rule    │  PASS / FAIL / UNCERTAIN,
                    │ engine (rules/engine) │  never confidence-thresholded
                    └─────────┬────────────┘
                              │
                    ┌─────────▼────────────┐
                    │ OverallResult +        │  stored verbatim, exposed via
                    │ evidence (DB, API)      │  /units/{id}/results & /evidence
                    └────────────────────────┘
```

## 3. Component responsibilities

| Component | Responsibility | Never does |
|---|---|---|
| `storage.py` | Preserve original bytes, hash, EXIF-normalize | Never overwrites the original |
| `quality/engine.py` | Blur / brightness / resolution gate | Never blocks silently — always states which metric failed |
| `vision/client.py` | ONE multimodal call per unit, structured JSON evidence | Never emits PASS/FAIL/compliance language |
| `ocr/engine.py` | Text extraction, raw + normalized | Never silently mutates the raw OCR text |
| `barcode/engine.py` | Deterministic decode (zbar) | Never guesses a partial/unreadable code |
| `geometry/engine.py` | Edge/seam proximity, orientation | Never assigns a compliance verdict |
| `rules/registry.py` + `resolver.py` | Versioned requirement definitions, applicability resolution | Never invented by the LLM |
| `rules/engine.py` | The ONLY place a verdict is decided | Never thresholds confidence directly into FAIL |
| `services/pipeline.py` | Orchestrates the above, fails open on any subsystem error | Never fabricates a result on failure |

## 4. Data flow (spec section 3)

`PHOTO → preprocessing → vision/OCR/barcode/geometry extraction →
structured VisualEvidenceBundle → authoritative requirement resolution →
deterministic rule engine → PASS/FAIL/UNCERTAIN → evidence record → API/UI/
audit log.`

This flow is implemented literally in `services/pipeline.py::run_analysis`.

## 5. Model flow — why one call per unit

`vision/client.py` sends every image for a unit plus the expected
identifiers in a single Groq API request (spec section 29). The model
is instructed (see `vision/prompts.py`) to return ONLY a JSON object of
`DetectedObject`-shaped entries — it is explicitly told it is not a
compliance authority and must never output PASS/FAIL. If the model can't
see something, it returns `detected: false` with its own confidence in the
absence, rather than omitting it — that's what lets the rule engine tell
"we looked and it's not there" apart from "we don't know."

## 6. OCR flow

Tesseract runs against a 2x-upscaled grayscale copy of the inference image
(`ocr/engine.py`). Histogram equalization is applied conditionally — only
when the image is genuinely low-contrast — because on well-exposed images
it was empirically found to distort small printed characters more than it
helped (see the fixture debugging notes in this repo's commit history /
scripts). Both `raw_text` and `normalized_text` are stored; a separate
`normalize_identifier()` (aggressive, uppercased, common-confusion-corrected)
is used ONLY for identifier matching (FNSKU/barcode), never shown to a user
as "what OCR read."

## 7. Barcode flow

`barcode/engine.py` wraps `pyzbar` (libzbar). A zbar decode is
deterministic — if it decodes, the value is authoritative; if two decode
passes (raw, then histogram-equalized) both fail, no barcode evidence is
produced and the relevant checks fall back to VLM/OCR evidence or
UNCERTAIN. Barcode/OCR disagreement on an identifier is never silently
resolved — see `rules/engine.py::check_fnsku`'s barcode-vs-expected-value
conflict path, which returns UNCERTAIN with the conflicting evidence shown.

## 8. Geometry engine

`geometry/engine.py` measures, it does not judge. For each detected object
with a bounding box, it computes:
- **Edge proximity**: distance from the box to the *detected package
  boundary* (the VLM's `PRODUCT` bounding box) when available, falling back
  to the raw image border with a note that the signal is weaker.
- **Seam overlap**: whether a long (`>= max(200px, image_min_dim/3)`),
  minimally-gapped (`maxLineGap=3`) Hough line crosses deep into the box's
  *interior* (a 16px inset — deliberately excluding the label's own
  printed border, which would otherwise always "overlap" its own bbox).
- **Orientation**: `cv2.minAreaRect` on the largest contour inside the box.

These thresholds are documented engineering heuristics (this section),
explicitly separated from compliance *rule* logic (`rules/registry.py`) per
spec section 10 — changing `EDGE_PROXIMITY_PX` changes measurement
precision, not what a rule means.

## 9. Rule engine

`rules/engine.py` is the single place PASS/FAIL/UNCERTAIN is decided. The
governing rule, enforced by code structure (not just convention):

```
if requirement.not_applicable:        return NOT_APPLICABLE
if evidence.proves_compliance:        return PASS
if evidence.proves_violation:         return FAIL
return UNCERTAIN
```

Confidence scores gate whether evidence counts as "proving" something
(`STRONG_DETECTION_CONF = 0.65`, etc.) — they are never thresholded
directly into FAIL. See `test_confidence_is_not_directly_thresholded_into_fail`
in `backend/tests/test_rule_engine.py` for a regression test of this
specific failure mode.

Overall aggregation (`rules/engine.py::evaluate`) is pure Python, not an
LLM call: any FAIL → overall FAIL; else any UNCERTAIN → overall UNCERTAIN;
else PASS.

## 10. Evidence model

Every `ComplianceCheckResult` (`schemas.py`) carries: the verdict, a
human-readable reason, a list of `EvidenceItem`s (each tagged with its
source — vlm / ocr / barcode / geometry — an image_id, and a bbox where
applicable), the rule_id/version/source that produced it, and — for
UNCERTAIN results — a `resolution_hint` telling the operator what
additional evidence would resolve it. The full merged `VisualEvidenceBundle`
that fed the rule engine is stored verbatim in the `visual_evidence` table
for audit, separate from the summarized `compliance_results` row.

## 11. Database schema

See `backend/app/models.py`. Every tenant-scoped table carries
`organization_id`. Dev/demo runs on SQLite with application-layer scoping
(`_scoped()` helper in `main.py`); `docs/postgres_rls.sql` has the
production row-level-security policy that makes this unconditional at the
database layer. See that file's header comment for why RLS is necessary in
addition to (not instead of) application-layer filtering.

## 12. Security

- No secrets committed; all configuration via environment variables
  (`app/config.py`, `.env.example`).
- Upload validation: content-type allowlist, size limit
  (`MAX_UPLOAD_BYTES`), rejected before touching disk.
- Auth in this build is a minimal header-based org/user identifier
  (`X-Org-Id`, `X-User-Id`) so the hackathon demo isn't gated on a full
  auth system — see `main.py::get_current_org` docstring for what to
  replace for production (verified session/JWT, not a trusted client
  header).
- Image storage paths are not currently signed URLs in the local-filesystem
  dev mode; `docs/postgres_rls.sql` section 4 describes the production
  requirement (presigned, short-lived URLs) for S3-compatible storage.

## 13. Uncertainty handling

UNCERTAIN is a first-class outcome, not a fallback. Every rule-engine
function has an explicit UNCERTAIN branch with its own reason and
`resolution_hint`. A requirement whose `visual_verifiability` is
`NOT_VISUALLY_VERIFIABLE` (spec section 50) *always* returns UNCERTAIN,
regardless of what the evidence shows — see
`rules/engine.py::check_not_visually_verifiable` and the
`PACKAGING_MATERIAL_THICKNESS` demo requirement in `rules/registry.py`,
included specifically to prove this path.

## 14. Failure handling (fail-open)

`services/pipeline.py::PipelinePendingReview` is raised (never a
fabricated result) when: every supplied image fails the quality gate, or
the vision API call fails for any reason (`vision/client.py::VisionUnavailableError`
— no key configured, network error, malformed response, timeout). The API
layer (`main.py::analyze_unit`) catches this, sets `processing_status =
PENDING_REVIEW`, writes an audit event with the reason, and returns HTTP
202 — never a 200 with invented data. This is verified live in this
repo's development notes: with no `ANTHROPIC_API_KEY` configured, a real
`POST /units/{id}/analyze` call against the running server returns exactly
this 202/PENDING_REVIEW response.

## 15. Cost model

One VLM call per unit (spec sections 29, 43) — never one call per check.
OCR, barcode decode, geometry, and the quality gate are all local/free
after the image is captured. `GET /metrics` reports `analyses_run`,
average/p50/p95 latency, tracked in-process in this build (swap for a real
metrics backend — Prometheus, etc. — for production).

## 16. Evaluation methodology

See `EVALUATION.md` and `evaluation/`. The evaluation runner
(`evaluation/runner.py`) executes the *exact same* pipeline code the API
uses (no evaluation-only shortcuts) against a manifest of units with
ground-truth per-check labels, and scores per-check precision/recall/F1
(FAIL as the positive class), uncertain rate, coverage, and a
false-pass/false-fail breakdown — never just one aggregate accuracy number.


## Authentication & tenant isolation

Round 2 adds an authentication boundary without changing the existing inspection pipeline.

- Accounts are created through `/auth/signup` and authenticated through `/auth/login`.
- Passwords use PBKDF2-HMAC-SHA256 with a per-user random salt.
- Protected API routes require a signed bearer session token.
- Each account receives its own organization/workspace ID; existing organization scoping therefore isolates units, images, results, evidence, reports and audit history.
- The frontend does not accept an operator-supplied organization ID for authorization. The backend derives the organization from the authenticated account.
- Logging out clears the browser session token. The existing vision, OCR, barcode, geometry, deterministic rule engine and review workflow are unchanged.
