from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from schemas import AulasState, NEXT_ACTION_BY_STATUS, AulaItem, PdfInfo

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = BACKEND_DIR.parents[1] if len(BACKEND_DIR.parents) > 1 else BACKEND_DIR
REPO_ROOT = Path(os.getenv("REPO_ROOT", str(DEFAULT_REPO_ROOT))).resolve()

PRIMARY_STATE_FILE = REPO_ROOT / "aula-pipeline" / "data" / "aulas.json"
FALLBACK_STATE_FILE = BACKEND_DIR / "data" / "aulas.json"
STATE_FILE = Path(
    os.getenv(
        "STATE_FILE",
        str(PRIMARY_STATE_FILE if PRIMARY_STATE_FILE.parent.exists() else FALLBACK_STATE_FILE),
    )
).resolve()

MODULOS_ROOT = Path(
    os.getenv(
        "MODULOS_ROOT",
        str(REPO_ROOT / "aulas_em_producao" / "modulos"),
    )
).resolve()

MODULE_RE = re.compile(r"^M(?P<m>\d+)_(?P<name>.+)$")
AULA_RE = re.compile(r"^M(?P<m>\d+)_A(?P<a>\d+)_(?P<name>.+)$")


def now_utc() -> datetime:
    return datetime.utcnow()


def humanize_slug(slug: str) -> str:
    keep_upper = {"DIU", "HPV", "RM", "RX", "US", "DDS", "THM", "TH", "SUA", "PTGI", "PCR"}
    stop = {"de", "da", "do", "das", "dos", "e", "em", "na", "no", "vs", "com", "para"}
    parts = [p for p in slug.split("_") if p]
    out: list[str] = []
    for i, token in enumerate(parts):
        t = token.upper()
        if t in keep_upper:
            out.append(t)
            continue
        low = token.lower()
        if i > 0 and low in stop:
            out.append(low)
            continue
        out.append(low.capitalize())
    return " ".join(out)


def load_state() -> AulasState:
    if STATE_FILE.exists():
        raw = STATE_FILE.read_text(encoding="utf-8")
        return AulasState.model_validate_json(raw)
    return AulasState(updated_at=now_utc(), aulas=[])


def save_state(state: AulasState) -> None:
    state.updated_at = now_utc()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump(mode="json")
    STATE_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def synchronize_with_filesystem(state: AulasState) -> AulasState:
    existing = {a.id: a for a in state.aulas}
    synced: list[AulaItem] = []

    if not MODULOS_ROOT.exists():
        state.aulas = []
        state.updated_at = now_utc()
        return state

    for mod_dir in sorted([p for p in MODULOS_ROOT.iterdir() if p.is_dir()]):
        mm = MODULE_RE.match(mod_dir.name)
        if not mm:
            continue
        mod_num = int(mm.group("m"))
        mod_nome = humanize_slug(mm.group("name"))

        aula_dirs = [p for p in mod_dir.iterdir() if p.is_dir()]
        for aula_dir in sorted(aula_dirs):
            am = AULA_RE.match(aula_dir.name)
            if not am:
                continue
            aula_num = int(am.group("a"))
            aula_tema = humanize_slug(am.group("name"))
            aula_id = f"M{mod_num}_A{aula_num}"

            prev = existing.get(aula_id)
            created_at = prev.created_at if prev else now_utc()
            status = prev.status if prev else "proximas_aulas"
            proxima_acao = prev.proxima_acao if prev else NEXT_ACTION_BY_STATUS[status]
            pendencias = prev.pendencias if prev else []
            historico = prev.historico if prev else []
            drive_folder_id = prev.drive_folder_id if prev else None
            drive_subfolders = prev.drive_subfolders if prev else {}

            paths = detect_aula_paths(aula_dir)
            pdf_info = compute_pdf_info(paths["artigos_dir"], prev.pdfs if prev else None)
            texto_preview = read_preview(paths["texto_aula"])

            synced.append(
                AulaItem(
                    id=aula_id,
                    modulo_num=mod_num,
                    modulo_nome=mod_nome,
                    aula_num=aula_num,
                    aula_tema=aula_tema,
                    status=status,
                    proxima_acao=proxima_acao,
                    pasta_relativa=str(aula_dir.relative_to(REPO_ROOT)),
                    pasta_absoluta=str(aula_dir.resolve()),
                    pendencias=pendencias,
                    pdfs=pdf_info,
                    arquivos={
                        "bibliografia_dir": _rel_or_none(paths["bibliografia_dir"]),
                        "livros_extraidos_dir": _rel_or_none(paths["livros_extraidos_dir"]),
                        "artigos_dir": _rel_or_none(paths["artigos_dir"]),
                        "texto_aula": _rel_or_none(paths["texto_aula"]),
                        "revisao": _rel_or_none(paths["revisao"]),
                        "pptx_final": _rel_or_none(paths["pptx_final"]),
                    },
                    texto_preview=texto_preview,
                    created_at=created_at,
                    updated_at=now_utc(),
                    historico=historico,
                    drive_folder_id=drive_folder_id,
                    drive_subfolders=drive_subfolders,
                )
            )

    synced.sort(key=lambda a: (a.modulo_num, a.aula_num))
    state.aulas = synced
    state.updated_at = now_utc()
    return state


def detect_aula_paths(aula_dir: Path) -> dict[str, Optional[Path]]:
    bibliografia_dir = aula_dir / "01_bibliografia"
    livros_extraidos_dir = aula_dir / "02_livros_extraidos"
    artigos_dir = aula_dir / "03_pdfs_artigos"

    texto_aula = aula_dir / "04_aula_texto.md"
    revisao = aula_dir / "06_revisao.md"

    module_prefix = aula_dir.name.split("_", 2)
    pptx_candidate = None
    if len(module_prefix) >= 2:
        pptx_candidate = aula_dir / f"{module_prefix[0]}_{module_prefix[1]}.pptx"

    return {
        "bibliografia_dir": bibliografia_dir if bibliografia_dir.exists() else None,
        "livros_extraidos_dir": livros_extraidos_dir if livros_extraidos_dir.exists() else None,
        "artigos_dir": artigos_dir if artigos_dir.exists() else None,
        "texto_aula": texto_aula if texto_aula.exists() else None,
        "revisao": revisao if revisao.exists() else None,
        "pptx_final": pptx_candidate if pptx_candidate and pptx_candidate.exists() else None,
    }


def compute_pdf_info(artigos_dir: Optional[Path], prev: Optional[PdfInfo] = None) -> PdfInfo:
    if artigos_dir and artigos_dir.exists():
        pdfs = sorted(p.name for p in artigos_dir.glob("*.pdf"))
    else:
        pdfs = []

    baixados = len(pdfs)
    total = baixados
    if prev:
        total = max(total, prev.total)

    return PdfInfo(total=total, baixados=baixados, nomes=pdfs)


def read_preview(text_path: Optional[Path], limit: int = 420) -> Optional[str]:
    if not text_path or not text_path.exists():
        return None
    content = text_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        return None
    content = re.sub(r"\s+", " ", content)
    if len(content) <= limit:
        return content
    return content[: limit - 3].rstrip() + "..."


def _rel_or_none(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    return str(path.relative_to(REPO_ROOT))


def write_bootstrap_state() -> AulasState:
    state = load_state()
    state = synchronize_with_filesystem(state)
    save_state(state)
    return state


if __name__ == "__main__":
    boot = write_bootstrap_state()
    print(json.dumps({"aulas": len(boot.aulas), "updated_at": boot.updated_at.isoformat()}, ensure_ascii=False))
