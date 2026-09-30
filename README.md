# Prep Manager

AI-powered visual preparation compliance verification for inbound products.

## The problem

A warehouse operator photographs a product after prepping it for shipment.
Someone (or something) needs to check: is it actually bagged, sealed,
labeled, and marked the way it's supposed to be? Getting this wrong either
ships a non-compliant unit or wastes a human's time re-checking things that
were fine.

## The solution — and what it deliberately does NOT do

Prep Manager is **not** a model that looks at a photo and says "PASS" or
"FAIL." That would mean trusting an LLM to both invent what compliance
requires *and* judge whether it was met — with no way to audit either
decision.

Instead:

1. **One multimodal call per unit** extracts structured visual evidence
   (what's in the photo — objects, regions, OCR guesses) — never a
   compliance judgment.
2. **Local, deterministic tools** (Tesseract OCR, zbar barcode decode,
   OpenCV geometry) add independently-verifiable evidence on top.
3. **Versioned, source-referenced rules** (never invented by the model)
   define what's actually required for this product.
4. **A deterministic Python rule engine** — not an LLM — is the only place
   a PASS / FAIL / UNCERTAIN verdict is decided.
5. Every verdict carries its evidence, its rule source, and (for
   UNCERTAIN) what additional photo would resolve it.

See `ARCHITECTURE.md` for the full data flow and component breakdown, and
`EVALUATION.md` for an honest (small, synthetic) evaluation report —
including the real bugs found and fixed while building this.

## What's implemented (working, tested)

- Image ingestion with hashing + EXIF normalization, originals never
  overwritten (`backend/app/storage.py`)
- Image quality gate — blur / brightness / resolution
  (`backend/app/quality/engine.py`)
- One-call-per-unit vision evidence extraction against the live Groq API, with fail-open behavior verified live
  (`backend/app/vision/client.py`)
- OCR (Tesseract), barcode decode (zbar), geometry analysis (OpenCV edge/
  seam detection using the detected package boundary)
- Versioned requirement registry + resolver
  (`backend/app/rules/registry.py`, `resolver.py`)
- Deterministic three-valued rule engine covering polybag, suffocation
  warning, FNSKU placement (incl. seam/edge detection), original-barcode
  coverage, expiry-date visibility, and handling marks
  (`backend/app/rules/engine.py`) — **16/16 unit tests passing**
- FastAPI backend with the full endpoint set from the spec, tenant-scoped
  via `X-Org-Id`, tested live end-to-end
- Evaluation framework — manifest, runner, per-check precision/recall/F1/
  confusion matrix, failure analysis (`evaluation/`)
- Operator web workspace — Dashboard KPIs, unit queue, reusable rulebooks,
  visual evidence with image boxes, human review, audit history, batch
  analysis, and printable/downloadable reports (`frontend/index.html`)
- Postgres row-level-security policy for production tenant isolation
  (`docs/postgres_rls.sql`)

## What's simplified for this build (and how to extend it)

- **OCR engine**: Tesseract instead of the spec-suggested PaddleOCR — swap
  by replacing `backend/app/ocr/engine.py` only; the `OCRResult` contract
  doesn't change.
- **Auth**: JWT-like bearer session authentication with per-user workspaces; the original header-based tenant demo has been replaced with account-backed authentication.
- **Dev database**: SQLite with application-layer tenant filtering; apply
  `docs/postgres_rls.sql` after switching `DATABASE_URL` to Postgres for
  database-enforced isolation.
- **Evaluation dataset**: 3 synthetic fixtures, not the 50+ real
  independently-labeled units the spec asks for — see `EVALUATION.md`'s
  "Honest framing" section.
- **Rule registry**: sample/demo rules only (spec section 53 explicitly
  says not to treat sample data as authoritative) — see the
  `_DEMO_SOURCE` marker in `backend/app/rules/registry.py`. Wiring in a
  real authoritative source means adding real `RequirementVersion`
  entries; the resolver/engine code doesn't change.

## Repository structure

```
prep-manager/
├── backend/app/
│   ├── main.py            FastAPI endpoints
│   ├── models.py          SQLAlchemy schema (tenant-scoped)
│   ├── schemas.py         Pydantic evidence/decision contracts
│   ├── config.py          env-driven configuration
│   ├── storage.py         image ingestion
│   ├── quality/            blur/brightness/resolution gate
│   ├── vision/              one-call VLM evidence extraction + prompt
│   ├── ocr/                Tesseract wrapper
│   ├── barcode/            zbar wrapper
│   ├── geometry/            OpenCV edge/seam/orientation analysis
│   ├── rules/                registry, resolver, deterministic engine
│   └── services/             pipeline orchestration
├── backend/tests/          rule-engine + geometry unit tests, fixtures
├── frontend/index.html      operator dashboard (static SPA)
├── evaluation/               manifest, runner, metrics, failure analysis
├── scripts/                  demo image generator, end-to-end demo runner
├── docs/                     ARCHITECTURE.md's companion SQL
├── ARCHITECTURE.md
├── EVALUATION.md
├── docker-compose.yml / Dockerfile
└── .env.example
```

## Running locally

### Backend

```powershell
python -m pip install -r backend/requirements.txt
python -m uvicorn app.main:app --reload --app-dir backend --env-file .env --port 8008
```

The live vision provider is Groq (`qwen/qwen3.8-27b`). If `GROQ_API_KEY` is
not configured, analysis fails open to `PENDING_REVIEW` rather than inventing
evidence.

### Frontend

```powershell
python -m http.server 5500 --directory frontend
```

Open `http://localhost:5500`. The sidebar defaults to `http://127.0.0.1:8008`.

### Tests

```bash
cd backend
python -m pytest tests/ -v
```

### End-to-end demo (no API key required)

```bash
python scripts/generate_demo_images.py   # regenerate synthetic fixtures
python scripts/run_demo.py               # runs the full pipeline; uses a
                                          # clearly-labeled simulated VLM
                                          # response if GROQ_API_KEY
                                          # isn't set, real Groq calls if the key is configured
```

### Evaluation

```bash
python evaluation/runner.py
cat evaluation/latest_report.json
```

### Docker

```bash
docker compose up --build
# backend (host): http://localhost:8008
# frontend: http://localhost:8080
```

Then, once, apply `docs/postgres_rls.sql` against the Postgres database for
production-grade tenant isolation.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/units` | Create a unit (product + work order metadata) |
| POST | `/units/{id}/images` | Upload photo(s) for a unit |
| POST | `/units/{id}/analyze` | Run the pipeline; 202 + PENDING_REVIEW on failure |
| GET | `/units` | List units (org-scoped) |
| GET | `/units/{id}` | Unit detail |
| GET | `/units/{id}/results` | Latest `OverallResult` |
| GET | `/units/{id}/evidence` | Full `VisualEvidenceBundle` for audit |
| POST | `/units/{id}/review` | Record a human review/override (original verdict preserved) |
| GET | `/units/{id}/audit` | Audit event log |
| GET | `/requirements` | The current rule registry |
| GET | `/health`, `/metrics` | Health check, latency stats |

Protected inspection endpoints require an authenticated bearer session. The account determines the workspace scope; clients do not choose an organization with `X-Org-Id`.

## Vision pipeline

See `ARCHITECTURE.md` §5 for the full explanation of why this is one call
per unit, what the model is (and is never) allowed to say, and how its
output is merged with local OCR/barcode/geometry evidence before the rule
engine ever sees it.

## Rules and uncertainty

See `ARCHITECTURE.md` §9 and §13. The short version: PASS/FAIL/UNCERTAIN
is decided by plain Python, never by thresholding a confidence score, and
a requirement that genuinely can't be judged from a photo (e.g. material
thickness) always returns UNCERTAIN — never a guessed PASS or FAIL.

## Authentication

Prep Manager now requires an authenticated account for inspections and stored evidence.

- **Sign up / Login:** Create an account with a name, email and password.
- **Private workspace:** Each account receives its own workspace/tenant, so inspection history, uploaded images, results, reports and audit events are isolated from other accounts.
- **Protected operations:** Unit creation, image upload, analysis, results, evidence, reports and reviews require an authenticated bearer session.
- **Password storage:** Passwords are stored as PBKDF2-HMAC-SHA256 hashes with per-user salts; plaintext passwords are never stored.
- **Session:** The browser keeps the signed session token for the current account. Logging out removes the local session and returns to the authentication screen.
- **Deployment:** Set `PREP_MANAGER_AUTH_SECRET` to a long random secret before deployment.

## Limitations

See `EVALUATION.md`'s "Known limitations" section — most importantly, the
evaluation numbers in this repo come from 3 synthetic fixtures, not a real
held-out photo set, and should not be read as real-world accuracy.

## Deployment

`docker-compose.yml` runs Postgres + the FastAPI backend + the static
frontend behind nginx. Apply `docs/postgres_rls.sql` once against the
Postgres database for tenant isolation. Set `GROQ_API_KEY` and `PREP_MANAGER_AUTH_SECRET` in the environment (never commit either — see `.env.example`).

## Demo

`python scripts/run_demo.py` runs three scenarios end-to-end and prints
every check's verdict + reason:

- `good_unit` — sealed polybag, legible warning, flat well-placed FNSKU →
  overall **UNCERTAIN** (correctly — one requirement in the demo ruleset is
  deliberately not-visually-verifiable, per spec section 50, so it can
  never contribute a false PASS)
- `fnsku_on_edge_unit` — the spec's flagship demo scenario (§56): FNSKU
  label straddling the package edge → **FAIL**, with the exact reason
  "FNSKU label placement overlaps the package edge, which prevents
  reliable scanning."
- `missing_polybag_unit` → **FAIL** on the polybag check

## Current local run

1. Copy `.env.example` to `.env` and add your Groq API key and Neon PostgreSQL connection string.
2. Install dependencies: `python -m pip install -r backend/requirements.txt`.
3. Start backend: `python -m uvicorn app.main:app --reload --app-dir backend --env-file .env --port 8008`.
4. Start frontend in another terminal: `python -m http.server 5500 --directory frontend`.
5. Open `http://localhost:5500`.

The frontend now includes Dashboard, All Units, Rulebooks, Reports, batch analysis, visual evidence, human review, and audit history.
