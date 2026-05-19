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
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv(
    "GOOGLE_OAUTH_CLIENT_SECRET",
    str(BACKEND_DIR / "credentials" / "oauth_client.json"),
)
GOOGLE_OAUTH_TOKEN_PATH = os.getenv(
    "GOOGLE_OAUTH_TOKEN_PATH",
    str(BACKEND_DIR / "credentials" / "token.json"),
)


def ensure_drive_env() -> tuple[bool, str]:
    if not DRIVE_ROOT_FOLDER_ID:
        return False, "DRIVE_ROOT_FOLDER_ID não configurado no .env"
    if not Path(GOOGLE_OAUTH_CLIENT_SECRET).exists():
        return False, f"Arquivo OAuth client não encontrado: {GOOGLE_OAUTH_CLIENT_SECRET}"
    return True, "ok"
