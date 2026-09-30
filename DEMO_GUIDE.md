# Prep Manager — Demo & Submission Guide

## What is included

- Compliance dashboard with PASS / FAIL / UNCERTAIN KPIs.
- Unit queue with status filters and batch analysis selection.
- Reusable rulebooks with built-in demo presets and custom rulebook creation.
- Groq multimodal evidence extraction using `qwen/qwen3.8-27b`.
- Local OCR (Tesseract), barcode detection (ZBar), and OpenCV geometry evidence.
- Built-in synthetic headset sample photo for demo/testing (`frontend/samples/headset-all-rules.png`).
- Evidence viewer that displays the inspected image and detected bounding boxes.
- “Why this result?” workflow with rule reasoning and resolution hints.
- Human review capture without deleting the original AI verdict.
- Per-unit audit history.
- Downloadable HTML reports and browser “Print / Save PDF” reports.

## Local setup (Windows)

Use Python 3.11 if you are following the tested Windows setup.

```powershell
python -m pip install -r backend/requirements.txt
```

Copy `.env.example` to `.env` and fill in:

```env
GROQ_API_KEY=YOUR_GROQ_KEY
PREP_MANAGER_VISION_MODEL=qwen/qwen3.8-27b
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

If using the default Windows Tesseract installation, no extra `PATH` command is required; the backend auto-detects `C:\Program Files\Tesseract-OCR\tesseract.exe`.

Start the backend in terminal 1:

```powershell
python -m uvicorn app.main:app --reload --app-dir backend --env-file .env --port 8008
```

Start the frontend in terminal 2:

```powershell
python -m http.server 5500 --directory frontend
```

Open:

`http://localhost:5500`

## Docker

Set `GROQ_API_KEY` in your shell, then:

```powershell
docker compose up --build
```

Frontend: `http://localhost:8080`  
Backend: `http://localhost:8008`

## Demo flow

1. Open **+ New Inspection**.
2. Enter SKU / ASIN / FNSKU and select a rulebook.
3. Add one to three clear product photos.
4. Click **Create & Upload**.
5. Open the unit and click **Analyze Prep**.
6. Open any check card to inspect the visual evidence and reason.
7. Use **Accept PASS**, **Accept FAIL**, or **Request another image** to record human review.
8. Use **Download Report** or **Print / Save PDF** for the inspection record.

## Important security note

Never commit `.env` or paste API keys / database passwords into source control. `.gitignore` already excludes `.env`, local databases, and runtime images.


### 0. Create an account / log in

Open the frontend at `http://localhost:8080`. The authentication screen appears before the existing Prep Manager workspace.

1. Select **Sign Up** and create an account, or use **Login** for an existing account.
2. After authentication, the existing dashboard and inspection workflow open unchanged.
3. **All Units**, **Reports**, uploaded images, results and audit history are scoped to the signed-in account.
4. Log out from the account panel to return to the authentication screen.
5. A logged-out browser cannot create inspections, upload images, analyze units, view stored evidence, or access reports.

For Docker, the backend is exposed to the browser at `http://localhost:8008` while the application container listens internally on port `8000`.

