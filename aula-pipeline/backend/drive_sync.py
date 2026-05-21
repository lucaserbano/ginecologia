from __future__ import annotations

from pathlib import Path
from typing import Optional

from drive_client import (
    create_folder,
    ensure_folder,
    get_file_by_id,
    list_children,
    trash_file,
    upload_file_to_folder,
)
from schemas import AulaItem, AulasState

AULA_SUBFOLDERS = [
    "01_bibliografia",
    "02_livros_extraidos",
    "03_pdfs_artigos",
    "04_aula_texto",
    "05_outline_slides",
    "06_revisao",
]


def _module_and_aula_folder_names(aula: AulaItem) -> tuple[str, str]:
    parts = Path(aula.pasta_relativa).parts
    # Esperado: aulas_em_producao/modulos/<modulo>/<aula>
    if len(parts) >= 4:
        return parts[-2], parts[-1]
    return f"M{aula.modulo_num}", aula.id


def bootstrap_drive_structure(
    state: AulasState,
    drive_service,
    drive_root_folder_id: str,
    force_relink: bool = False,
    max_aulas: Optional[int] = None,
) -> dict:
    modules_cache: dict[str, str] = {}
    created_modules = 0
    linked_aulas = 0
    skipped_ready = 0
    aulas_processed = 0

    for aula in state.aulas:
        if max_aulas is not None and aulas_processed >= max_aulas:
            break

        if not force_relink and _aula_drive_ready(aula):
            skipped_ready += 1
            continue

        module_folder_name, aula_folder_name = _module_and_aula_folder_names(aula)

        module_id = modules_cache.get(module_folder_name)
        if not module_id:
            m = ensure_folder(drive_service, drive_root_folder_id, module_folder_name)
            module_id = m["id"]
            modules_cache[module_folder_name] = module_id
            created_modules += 1

        aula_folder = None
        if aula.drive_folder_id and not force_relink:
            cached = get_file_by_id(drive_service, aula.drive_folder_id)
            if (
                cached
                and cached.get("mimeType") == "application/vnd.google-apps.folder"
                and module_id in (cached.get("parents") or [])
            ):
                aula_folder = cached

        if not aula_folder:
            aula_folder = ensure_folder(drive_service, module_id, aula_folder_name)

        if aula.drive_folder_id != aula_folder["id"]:
            linked_aulas += 1
        aula.drive_folder_id = aula_folder["id"]

        subfolder_map = _ensure_aula_subfolders(
            drive_service=drive_service,
            aula_folder_id=aula_folder["id"],
        )
        aula.drive_subfolders = subfolder_map
        aulas_processed += 1

    return {
        "modules_touched": len(modules_cache),
        "aulas_touched": len(state.aulas),
        "aulas_processed": aulas_processed,
        "aulas_skipped_ready": skipped_ready,
        "modules_created_or_found": created_modules,
        "aulas_linked_or_updated": linked_aulas,
        "force_relink": force_relink,
        "max_aulas": max_aulas,
    }


def list_aula_drive_files(aula: AulaItem, drive_service) -> list[dict]:
    if not aula.drive_folder_id:
        return []

    root_children = list_children(drive_service, aula.drive_folder_id)
    all_items: list[dict] = []

    for item in root_children:
        all_items.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "mimeType": item.get("mimeType"),
                "modifiedTime": item.get("modifiedTime"),
                "size": item.get("size"),
                "webViewLink": item.get("webViewLink"),
                "parentLabel": "root",
            }
        )
        if item.get("mimeType") == "application/vnd.google-apps.folder":
            for child in list_children(drive_service, item["id"]):
                all_items.append(
                    {
                        "id": child.get("id"),
                        "name": child.get("name"),
                        "mimeType": child.get("mimeType"),
                        "modifiedTime": child.get("modifiedTime"),
                        "size": child.get("size"),
                        "webViewLink": child.get("webViewLink"),
                        "parentLabel": item.get("name"),
                    }
                )

    return all_items


def upload_local_file_for_aula(
    aula: AulaItem,
    drive_service,
    local_path: Path,
    target_subfolder: Optional[str] = None,
    target_name: Optional[str] = None,
) -> dict:
    if not local_path.exists():
        raise FileNotFoundError(f"Arquivo local não encontrado: {local_path}")
    if not aula.drive_folder_id:
        raise ValueError("Aula sem drive_folder_id. Rode bootstrap do Drive primeiro.")

    target_folder_id = aula.drive_folder_id

    if target_subfolder:
        if not aula.drive_subfolders or target_subfolder not in aula.drive_subfolders:
            sf = ensure_folder(drive_service, aula.drive_folder_id, target_subfolder)
            aula.drive_subfolders = dict(aula.drive_subfolders or {})
            aula.drive_subfolders[target_subfolder] = sf["id"]
        target_folder_id = aula.drive_subfolders[target_subfolder]

    return upload_file_to_folder(
        drive_service,
        local_path=local_path,
        folder_id=target_folder_id,
        target_name=target_name,
    )


def cleanup_duplicates_for_aula(drive_service, aula: AulaItem, dry_run: bool = False) -> dict:
    """Dentro de cada subpasta da aula no Drive, mantem apenas o arquivo
    com `modifiedTime` mais recente para cada nome unico; trasha os demais.

    Pastas e arquivos sem nome sao ignorados. Retorna sumario com a
    lista de arquivos trashed (so para auditoria).
    """
    if not aula.drive_folder_id:
        return {"aula_id": aula.id, "skipped": True, "reason": "sem drive_folder_id"}

    subfolder_ids: dict[str, str] = dict(aula.drive_subfolders or {})
    if not subfolder_ids:
        # Bootstrap leve: descobre as subpastas atualmente existentes.
        for child in list_children(drive_service, aula.drive_folder_id):
            if child.get("mimeType") == "application/vnd.google-apps.folder":
                subfolder_ids[child.get("name")] = child.get("id")

    trashed: list[dict] = []
    inspected = 0
    for sub_name, sub_id in subfolder_ids.items():
        children = [c for c in list_children(drive_service, sub_id)
                    if c.get("mimeType") != "application/vnd.google-apps.folder"]
        inspected += len(children)
        by_name: dict[str, list[dict]] = {}
        for child in children:
            name = child.get("name")
            if not name:
                continue
            by_name.setdefault(name, []).append(child)

        for name, group in by_name.items():
            if len(group) <= 1:
                continue
            group.sort(key=lambda c: c.get("modifiedTime") or "", reverse=True)
            keep = group[0]
            for victim in group[1:]:
                victim_id = victim.get("id")
                if not victim_id:
                    continue
                if not dry_run:
                    try:
                        trash_file(drive_service, victim_id)
                    except Exception as exc:
                        trashed.append({
                            "aula_id": aula.id, "subfolder": sub_name, "name": name,
                            "id": victim_id, "trashed": False, "error": str(exc),
                        })
                        continue
                trashed.append({
                    "aula_id": aula.id, "subfolder": sub_name, "name": name,
                    "id": victim_id, "modifiedTime": victim.get("modifiedTime"),
                    "trashed": not dry_run,
                    "kept_id": keep.get("id"),
                })

    return {
        "aula_id": aula.id,
        "inspected": inspected,
        "trashed_count": sum(1 for t in trashed if t.get("trashed")),
        "errors": sum(1 for t in trashed if t.get("error")),
        "dry_run": dry_run,
        "details": trashed,
    }


def cleanup_duplicates_all(drive_service, state: AulasState, dry_run: bool = False) -> dict:
    """Roda cleanup_duplicates_for_aula em cada aula com drive_folder_id."""
    per_aula = []
    total_inspected = 0
    total_trashed = 0
    total_errors = 0
    for aula in state.aulas:
        if not aula.drive_folder_id:
            continue
        report = cleanup_duplicates_for_aula(drive_service, aula, dry_run=dry_run)
        per_aula.append({
            "aula_id": report["aula_id"],
            "inspected": report.get("inspected", 0),
            "trashed_count": report.get("trashed_count", 0),
            "errors": report.get("errors", 0),
        })
        total_inspected += report.get("inspected", 0)
        total_trashed += report.get("trashed_count", 0)
        total_errors += report.get("errors", 0)
    return {
        "aulas": len(per_aula),
        "inspected": total_inspected,
        "trashed_count": total_trashed,
        "errors": total_errors,
        "dry_run": dry_run,
        "per_aula": per_aula,
    }


def _aula_drive_ready(aula: AulaItem) -> bool:
    if not aula.drive_folder_id:
        return False
    if not aula.drive_subfolders:
        return False
    return all(name in aula.drive_subfolders for name in AULA_SUBFOLDERS)


def _ensure_aula_subfolders(drive_service, aula_folder_id: str) -> dict[str, str]:
    current = list_children(drive_service, aula_folder_id)
    subfolders = {
        item.get("name"): item.get("id")
        for item in current
        if item.get("mimeType") == "application/vnd.google-apps.folder"
    }
    for sub in AULA_SUBFOLDERS:
        if sub in subfolders:
            continue
        created = create_folder(drive_service, aula_folder_id, sub)
        subfolders[sub] = created["id"]
    return {sub: subfolders[sub] for sub in AULA_SUBFOLDERS if sub in subfolders}
