from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = BACKEND_DIR.parent
REPO_ROOT = PIPELINE_DIR.parent

# Carrega .env da pasta aula-pipeline e fallback do backend.
load_dotenv(PIPELINE_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

DRIVE_ROOT_FOLDER_ID = os.getenv("DRIVE_ROOT_FOLDER_ID", "").strip()
GOOGLE_DRIVE_AUTH_MODE = os.getenv("GOOGLE_DRIVE_AUTH_MODE", "oauth").strip().lower()
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    str(BACKEND_DIR / "credentials" / "service_account.json"),
).strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv(
    "GOOGLE_OAUTH_CLIENT_SECRET",
    str(BACKEND_DIR / "credentials" / "oauth_client.json"),
)
GOOGLE_OAUTH_TOKEN_PATH = os.getenv(
    "GOOGLE_OAUTH_TOKEN_PATH",
    str(BACKEND_DIR / "credentials" / "token.json"),
)
OPEN_FOLDER_ACTION_ENABLED = os.getenv("OPEN_FOLDER_ACTION_ENABLED", "1").strip() in {"1", "true", "TRUE", "yes"}
ALLOWED_ORIGINS = [
    item.strip()
    for item in os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8787,http://localhost:8787").split(",")
    if item.strip()
]
ENABLE_AI_ACTIONS = os.getenv("ENABLE_AI_ACTIONS", "0").strip() in {"1", "true", "TRUE", "yes"}
AI_BACKEND = os.getenv("AI_BACKEND", "vertex").strip().lower()
AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "90").strip())

# Vertex AI (recomendado para usar créditos Google Cloud)
VERTEX_PROJECT_ID = (
    os.getenv("VERTEX_PROJECT_ID", "").strip()
    or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    or os.getenv("GCP_PROJECT", "").strip()
)
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1").strip()
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-2.5-flash").strip()

# OpenRouter (opcional/fallback)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4").strip()
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions").strip()
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "https://lucaserbano.github.io/ginecologia/").strip()
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "Gineco Kanban").strip()


def ensure_drive_env() -> tuple[bool, str]:
    if not DRIVE_ROOT_FOLDER_ID:
        return False, "DRIVE_ROOT_FOLDER_ID não configurado no .env"

    if GOOGLE_DRIVE_AUTH_MODE == "service_account":
        # Em Cloud Run, pode usar identidade anexada ao serviço (ADC) sem arquivo/chave.
        return True, "ok"

    if not Path(GOOGLE_OAUTH_CLIENT_SECRET).exists():
        return False, f"Arquivo OAuth client não encontrado: {GOOGLE_OAUTH_CLIENT_SECRET}"
    return True, "ok"
