from __future__ import annotations

import os
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = BACKEND_DIR.parents[1] if len(BACKEND_DIR.parents) > 1 else BACKEND_DIR
REPO_ROOT = Path(os.getenv("REPO_ROOT", str(DEFAULT_REPO_ROOT))).resolve()

AGENT_DIR_CANDIDATES = [
    REPO_ROOT / "agents",
    BACKEND_DIR / "agents",
]
TEMPLATE_DIR_CANDIDATES = [
    REPO_ROOT / "aulas" / "templates",
    BACKEND_DIR / "aulas" / "templates",
]


def load_agent_prompts(*filenames: str) -> str:
    chunks: list[str] = []
    for filename in filenames:
        text = _read_first_existing(AGENT_DIR_CANDIDATES, filename)
        if text:
            chunks.append(f"## Prompt-base: {filename}\n{text}")
    return "\n\n".join(chunks).strip()


def load_template(filename: str) -> str:
    return _read_first_existing(TEMPLATE_DIR_CANDIDATES, filename)


def _read_first_existing(candidates: list[Path], filename: str) -> str:
    for base in candidates:
        path = base / filename
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    return ""
