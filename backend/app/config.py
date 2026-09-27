import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during minimal imports
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env", override=False)

STORAGE_DIR = Path(os.getenv("PREP_MANAGER_STORAGE_DIR", BASE_DIR / "storage" / "images"))
if not STORAGE_DIR.is_absolute():
    STORAGE_DIR = BASE_DIR / STORAGE_DIR
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'storage' / 'prep_manager.db'}")
# SQLAlchemy defaults the bare postgresql:// URL to psycopg2. This project
# ships psycopg 3, so normalize a bare PostgreSQL URL to the psycopg dialect.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]

# Groq vision model — one multimodal call per unit.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
VISION_MODEL = os.getenv("PREP_MANAGER_VISION_MODEL", os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"))
VISION_MAX_TOKENS = int(os.getenv("PREP_MANAGER_VISION_MAX_TOKENS", "4000"))

MAX_UPLOAD_BYTES = int(os.getenv("PREP_MANAGER_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Quality gate thresholds — engineering heuristics, documented and isolated
# from compliance rules.
BLUR_VARIANCE_MIN = float(os.getenv("PREP_MANAGER_BLUR_MIN", "60.0"))
BRIGHTNESS_MIN = float(os.getenv("PREP_MANAGER_BRIGHTNESS_MIN", "25.0"))
BRIGHTNESS_MAX = float(os.getenv("PREP_MANAGER_BRIGHTNESS_MAX", "230.0"))
MIN_RESOLUTION_PX = int(os.getenv("PREP_MANAGER_MIN_RESOLUTION", "400"))

# Windows-friendly Tesseract configuration. The executable can still be
# overridden through TESSERACT_CMD for custom installations.
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

RULE_ENGINE_VERSION = "RULES_V1"
PROMPT_VERSION = "VISION_PROMPT_V1"
SOFTWARE_VERSION = "prep-manager-0.2.0"
