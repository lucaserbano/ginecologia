from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from drive_client import FOLDER_MIME, build_drive, list_children
from settings import DRIVE_ROOT_FOLDER_ID, ensure_drive_env


@dataclass
class FolderDuplicateGroup:
    parent_id: str
    folder_name: str
    folders: list[dict]
    keeper_id: str | None
    deletable_ids: list[str]
    reason: str


def _default_aulas_json_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "aulas.json"


def load_protected_folder_ids(aulas_json_path: Path) -> set[str]:
    if not aulas_json_path.exists():
        return set()

    payload = json.loads(aulas_json_path.read_text(encoding="utf-8"))
    aulas = payload.get("aulas", [])

    protected: set[str] = set()
    for aula in aulas:
        folder_id = aula.get("drive_folder_id")
        if isinstance(folder_id, str) and folder_id.strip():
            protected.add(folder_id.strip())

        subfolders = aula.get("drive_subfolders") or {}
        if isinstance(subfolders, dict):
            for sub_id in subfolders.values():
                if isinstance(sub_id, str) and sub_id.strip():
                    protected.add(sub_id.strip())

    return protected


def scan_duplicate_groups(
    service,
    root_folder_id: str,
    progress_every: int = 100,
) -> list[tuple[str, str, list[dict]]]:
    groups: list[tuple[str, str, list[dict]]] = []
    visited: set[str] = set()
    queue: list[str] = [root_folder_id]
    scanned = 0

    while queue:
        parent_id = queue.pop()
        if parent_id in visited:
            continue
        visited.add(parent_id)
        scanned += 1

        children = list_children(service, parent_id)
        folders = [
            item
            for item in children
            if item.get("mimeType") == FOLDER_MIME and isinstance(item.get("id"), str)
        ]

        for folder in folders:
            folder_id = folder["id"]
            if folder_id not in visited:
                queue.append(folder_id)

        by_name: dict[str, list[dict]] = defaultdict(list)
        for folder in folders:
            name = str(folder.get("name") or "").strip()
            by_name[name].append(folder)

        for folder_name, same_name_folders in by_name.items():
            if len(same_name_folders) > 1:
                groups.append((parent_id, folder_name, same_name_folders))

        if progress_every > 0 and (scanned % progress_every) == 0:
            print(
                f"[scan] pastas verificadas={scanned} fila={len(queue)} "
                f"grupos_duplicados={len(groups)}",
                flush=True,
            )

    return groups


def _sort_newest_first(folders: Iterable[dict]) -> list[dict]:
    return sorted(
        folders,
        key=lambda item: str(item.get("modifiedTime") or ""),
        reverse=True,
    )


def choose_action_for_group(
    parent_id: str,
    folder_name: str,
    folders: list[dict],
    protected_ids: set[str],
    strategy: str,
) -> FolderDuplicateGroup:
    protected_in_group = [f for f in folders if f["id"] in protected_ids]

    if strategy == "protected":
        if len(protected_in_group) == 1:
            keeper = protected_in_group[0]["id"]
            deletable = [f["id"] for f in folders if f["id"] != keeper]
            return FolderDuplicateGroup(
                parent_id=parent_id,
                folder_name=folder_name,
                folders=folders,
                keeper_id=keeper,
                deletable_ids=deletable,
                reason="safe_single_protected_reference",
            )
        if len(protected_in_group) > 1:
            return FolderDuplicateGroup(
                parent_id=parent_id,
                folder_name=folder_name,
                folders=folders,
                keeper_id=None,
                deletable_ids=[],
                reason="ambiguous_multiple_protected_references",
            )
        return FolderDuplicateGroup(
            parent_id=parent_id,
            folder_name=folder_name,
            folders=folders,
            keeper_id=None,
            deletable_ids=[],
            reason="ambiguous_no_protected_reference",
        )

    sorted_folders = _sort_newest_first(folders)
    if strategy == "newest":
        keeper = sorted_folders[0]["id"]
        deletable = [f["id"] for f in folders if f["id"] != keeper]
        return FolderDuplicateGroup(
            parent_id=parent_id,
            folder_name=folder_name,
            folders=folders,
            keeper_id=keeper,
            deletable_ids=deletable,
            reason="heuristic_keep_newest",
        )

    if strategy == "oldest":
        keeper = sorted_folders[-1]["id"]
        deletable = [f["id"] for f in folders if f["id"] != keeper]
        return FolderDuplicateGroup(
            parent_id=parent_id,
            folder_name=folder_name,
            folders=folders,
            keeper_id=keeper,
            deletable_ids=deletable,
            reason="heuristic_keep_oldest",
        )

    raise ValueError(f"Estratégia inválida: {strategy}")


def trash_folder(service, folder_id: str) -> None:
    (
        service.files()
        .update(
            fileId=folder_id,
            body={"trashed": True},
            supportsAllDrives=True,
            fields="id,trashed",
        )
        .execute()
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Detecta e remove pastas duplicadas no Google Drive (mesmo nome no mesmo pai). "
            "Por padrão, apenas simula."
        )
    )
    parser.add_argument(
        "--root-folder-id",
        default=DRIVE_ROOT_FOLDER_ID,
        help="ID da pasta raiz no Drive. Padrão: DRIVE_ROOT_FOLDER_ID do .env",
    )
    parser.add_argument(
        "--aulas-json",
        default=str(_default_aulas_json_path()),
        help="Caminho do aulas.json para proteger IDs já vinculados.",
    )
    parser.add_argument(
        "--strategy",
        choices=["protected", "newest", "oldest"],
        default="protected",
        help=(
            "protected: só apaga quando existe 1 único ID protegido no grupo; "
            "newest: mantém o mais recente; oldest: mantém o mais antigo."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Executa de verdade (sem este flag, roda em dry-run).",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Mostra progresso a cada N pastas varridas (0 desativa).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.root_folder_id:
        ok, message = ensure_drive_env()
        if not ok:
            print(f"ERRO: {message}")
            return 2
        print("ERRO: root folder id não informado.")
        return 2

    aulas_json_path = Path(args.aulas_json).expanduser().resolve()
    protected_ids = load_protected_folder_ids(aulas_json_path)

    print(f"Root folder id: {args.root_folder_id}")
    print(f"Aulas JSON: {aulas_json_path}")
    print(f"Estratégia: {args.strategy}")
    print(f"Modo: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"IDs protegidos carregados: {len(protected_ids)}")
    print("-" * 72)

    service = build_drive(interactive=False)
    raw_groups = scan_duplicate_groups(
        service,
        args.root_folder_id,
        progress_every=args.progress_every,
    )
    print(f"[scan] concluído. grupos_duplicados={len(raw_groups)}", flush=True)

    analyzed: list[FolderDuplicateGroup] = [
        choose_action_for_group(
            parent_id=parent_id,
            folder_name=folder_name,
            folders=folders,
            protected_ids=protected_ids,
            strategy=args.strategy,
        )
        for parent_id, folder_name, folders in raw_groups
    ]

    deletions_planned = sum(len(group.deletable_ids) for group in analyzed)
    deletions_done = 0
    groups_with_safe_delete = sum(1 for group in analyzed if group.deletable_ids)

    for idx, group in enumerate(analyzed, start=1):
        print(
            f"[{idx}/{len(analyzed)}] parent={group.parent_id} "
            f'name="{group.folder_name}" total={len(group.folders)} '
            f"keeper={group.keeper_id or '-'} reason={group.reason}"
        )

        if group.deletable_ids:
            print(f"  deletar: {', '.join(group.deletable_ids)}")
        else:
            ids = ", ".join(folder["id"] for folder in group.folders)
            print(f"  manter (sem ação): {ids}")

    print("-" * 72)
    print(f"Grupos duplicados encontrados: {len(analyzed)}")
    print(f"Grupos com ação de limpeza: {groups_with_safe_delete}")
    print(f"Pastas marcadas para remoção: {deletions_planned}")

    if not args.apply:
        print("Dry-run finalizado. Use --apply para mover as duplicadas para a lixeira.")
        return 0

    for group in analyzed:
        for folder_id in group.deletable_ids:
            trash_folder(service, folder_id)
            deletions_done += 1
            print(f"  [OK] pasta movida para lixeira: {folder_id}")

    print(f"Remoções concluídas: {deletions_done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
