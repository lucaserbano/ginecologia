from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from schemas import AulaItem
from settings import ensure_drive_env


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = BACKEND_DIR.parents[1] if len(BACKEND_DIR.parents) > 1 else BACKEND_DIR
REPO_ROOT = Path(os.getenv("REPO_ROOT", str(DEFAULT_REPO_ROOT))).resolve()

ARTIFACT_TARGETS = {
    "01_bibliografia.md": {"local_dir": "01_bibliografia", "drive_dir": "01_bibliografia"},
    "capitulos_livros.md": {"local_dir": "01_bibliografia", "drive_dir": "01_bibliografia"},
    "diretrizes_consensos.md": {"local_dir": "01_bibliografia", "drive_dir": "01_bibliografia"},
    "pubmed_busca.md": {"local_dir": "01_bibliografia", "drive_dir": "01_bibliografia"},
    "uptodate.md": {"local_dir": "01_bibliografia", "drive_dir": "01_bibliografia"},
    "04_aula_texto.md": {"local_dir": None, "drive_dir": "04_aula_texto"},
    "05_outline_slides.md": {"local_dir": None, "drive_dir": "05_outline_slides"},
    "06_revisao.md": {"local_dir": None, "drive_dir": "06_revisao"},
}


@dataclass
class ArtifactWriteResult:
    filename: str
    local_path: Optional[str] = None
    drive_file: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)


def persist_ai_artifact(aula: AulaItem, filename: str, content: str) -> ArtifactWriteResult:
    aula.ai_artifacts[filename] = content
    result = ArtifactWriteResult(filename=filename)
    target = ARTIFACT_TARGETS.get(filename, {"local_dir": None, "drive_dir": None})

    local_path = _write_local_artifact(aula, filename, content, target.get("local_dir"), result)
    _upload_artifact_to_drive(aula, filename, content, local_path, target.get("drive_dir"), result)

    return result


def format_artifact_result(result: ArtifactWriteResult) -> str:
    parts: list[str] = []
    if result.local_path:
        parts.append(f"arquivo local: {result.local_path}")
    if result.drive_file:
        name = result.drive_file.get("name") or result.filename
        parts.append(f"Drive: {name}")
    if result.warnings:
        parts.append("avisos: " + "; ".join(result.warnings))
    if not parts:
        parts.append("artefato salvo apenas no estado JSON")
    return " ".join(parts)


def _write_local_artifact(
    aula: AulaItem,
    filename: str,
    content: str,
    local_dir: Optional[str],
    result: ArtifactWriteResult,
) -> Optional[Path]:
    aula_dir = Path(aula.pasta_absoluta)
    if not aula_dir.exists():
        result.warnings.append("pasta local da aula indisponível")
        return None

    target_dir = aula_dir / local_dir if local_dir else aula_dir
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        path.write_text(content, encoding="utf-8")
        result.local_path = _relative_or_absolute(path)
        _update_aula_file_pointers(aula, filename, path)
        return path
    except Exception as exc:
        result.warnings.append(f"falha ao gravar arquivo local: {exc}")
        return None


def _upload_artifact_to_drive(
    aula: AulaItem,
    filename: str,
    content: str,
    local_path: Optional[Path],
    drive_dir: Optional[str],
    result: ArtifactWriteResult,
) -> None:
    ok, message = ensure_drive_env()
    if not ok:
        result.warnings.append(f"Drive não configurado: {message}")
        return
    if not aula.drive_folder_id:
        result.warnings.append("aula sem pasta Drive vinculada")
        return

    tmp_path: Optional[Path] = None
    upload_path = local_path
    try:
        from drive_client import build_drive
        from drive_sync import upload_local_file_for_aula

        if not upload_path or not upload_path.exists():
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".md", encoding="utf-8") as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            upload_path = tmp_path

        service = build_drive(interactive=False)
        result.drive_file = upload_local_file_for_aula(
            aula=aula,
            drive_service=service,
            local_path=upload_path,
            target_subfolder=drive_dir,
            target_name=filename,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "DriveAuthError":
            result.warnings.append(f"Drive sem autorização: {exc}")
        else:
            result.warnings.append(f"falha no upload Drive: {exc}")
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _update_aula_file_pointers(aula: AulaItem, filename: str, path: Path) -> None:
    rel = _relative_or_absolute(path)
    if filename == "01_bibliografia.md":
        aula.arquivos.bibliografia_dir = _relative_or_absolute(path.parent)
    elif filename == "04_aula_texto.md":
        aula.arquivos.texto_aula = rel
    elif filename == "06_revisao.md":
        aula.arquivos.revisao = rel


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except Exception:
        return str(path.resolve())
