from __future__ import annotations

from typing import Optional

from drive_client import build_drive, download_file_bytes, list_children
from schemas import AulaItem


def read_markdown_file_from_drive(aula: AulaItem, filename: str, subfolder: Optional[str] = None) -> str:
    if not aula.drive_folder_id:
        return ""

    try:
        service = build_drive(interactive=False)
    except Exception:
        return ""

    folder_id = _resolve_folder_id(service, aula, subfolder)
    if not folder_id:
        return ""

    candidates = [item for item in list_children(service, folder_id) if item.get("name") == filename]
    if not candidates:
        return ""

    # Se houver duplicatas no Drive, usa a mais recente.
    candidates.sort(key=lambda item: item.get("modifiedTime", ""), reverse=True)
    chosen = candidates[0]
    return _download_markdown(service, chosen.get("id"))


def read_markdown_group_from_drive(aula: AulaItem, subfolder: str, filenames: list[str]) -> str:
    if not filenames:
        return ""

    if not aula.drive_folder_id:
        return ""

    try:
        service = build_drive(interactive=False)
    except Exception:
        return ""

    folder_id = _resolve_folder_id(service, aula, subfolder)
    if not folder_id:
        return ""

    by_name = {item.get("name"): item for item in list_children(service, folder_id)}
    chunks: list[str] = []
    for name in filenames:
        item = by_name.get(name)
        content = _download_markdown(service, item.get("id")) if item else ""
        if content:
            chunks.append(f"# Fonte: {name}\n\n{content}")
    return "\n\n---\n\n".join(chunks)


def _resolve_folder_id(service, aula: AulaItem, subfolder: Optional[str]) -> Optional[str]:
    if not subfolder:
        return aula.drive_folder_id

    if aula.drive_subfolders and subfolder in aula.drive_subfolders:
        return aula.drive_subfolders[subfolder]

    items = list_children(service, aula.drive_folder_id)
    for item in items:
        if item.get("mimeType") == "application/vnd.google-apps.folder" and item.get("name") == subfolder:
            aula.drive_subfolders = dict(aula.drive_subfolders or {})
            aula.drive_subfolders[subfolder] = item.get("id")
            return item.get("id")
    return None


def _download_markdown(service, file_id: Optional[str]) -> str:
    if not file_id:
        return ""
    try:
        raw = download_file_bytes(service, file_id)
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
