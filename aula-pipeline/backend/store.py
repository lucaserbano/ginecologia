from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import firestore_store
from schemas import AulasState, NEXT_ACTION_BY_STATUS, AulaItem, PdfInfo

logger = logging.getLogger("store")

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


LEGACY_STATUS_MAP = {
    # Fluxo antigo (pré-refactor de 9 colunas) -> novo.
    "aguardando_aprovacao_fontes": "bibliografia_pronta",
    "aguardando_pdfs": "bibliografia_pronta",
    "pdfs_adicionados": "pdfs_baixados",
    "texto_em_producao": "pdfs_baixados",
    "texto_pronto_revisao": "texto_feito",
    "texto_revisado": "texto_editado",
    "slides_em_producao": "texto_editado",
    "pptx_pronto": "pptx_gerado",
    "revisao_final": "pptx_finalizado",
    "concluida": "pptx_na_pasta_final",
}


def _migrate_payload(payload: dict) -> dict:
    """Aplica migração de statuses legados num payload AulasState bruto."""
    from schemas import STATUS_COLUMNS
    valid_statuses = {k for k, _ in STATUS_COLUMNS}

    def _migrate(value):
        if value in LEGACY_STATUS_MAP:
            return LEGACY_STATUS_MAP[value]
        if value not in valid_statuses:
            return "proximas_aulas"
        return value

    for aula in payload.get("aulas", []):
        aula["status"] = _migrate(aula.get("status"))
        arquivos = aula.get("arquivos") or {}
        arquivos.pop("revisao", None)
        aula["arquivos"] = arquivos
        for evento in aula.get("historico") or []:
            if "de_status" in evento:
                evento["de_status"] = _migrate(evento.get("de_status"))
            if "para_status" in evento:
                evento["para_status"] = _migrate(evento.get("para_status"))
    return payload


def _load_state_from_file() -> AulasState:
    if STATE_FILE.exists():
        raw = STATE_FILE.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            return AulasState(updated_at=now_utc(), aulas=[])
        payload = _migrate_payload(payload)
        return AulasState.model_validate(payload)
    return AulasState(updated_at=now_utc(), aulas=[])


def load_state() -> AulasState:
    """Carrega o estado: Firestore (fonte de verdade) → fallback para JSON local.

    Se Firestore está disponível mas vazio, faz bootstrap a partir do JSON
    empacotado (one-shot migration). Isso roda automaticamente no primeiro
    deploy que tiver Firestore habilitado.
    """
    if firestore_store.is_available():
        try:
            aulas = firestore_store.list_aulas()
        except Exception as exc:
            logger.warning("Falha lendo Firestore, caindo para JSON: %s", exc)
            return _load_state_from_file()

        if not aulas:
            # Coleção vazia: tenta migrar do JSON empacotado.
            file_state = _load_state_from_file()
            if file_state.aulas:
                logger.info("Firestore vazio. Migrando %d aulas do JSON empacotado.", len(file_state.aulas))
                try:
                    firestore_store.upsert_many(file_state.aulas)
                    return file_state
                except Exception as exc:
                    logger.warning("Migração Firestore falhou: %s", exc)
                    return file_state
            return AulasState(updated_at=now_utc(), aulas=[])

        aulas.sort(key=lambda a: (a.modulo_num, a.aula_num))
        return AulasState(updated_at=now_utc(), aulas=aulas)

    return _load_state_from_file()


def save_state(state: AulasState) -> None:
    """Persiste o estado completo (todas as aulas). Em Firestore usa batch."""
    state.updated_at = now_utc()
    if firestore_store.is_available():
        try:
            firestore_store.upsert_many(state.aulas)
            return
        except Exception as exc:
            logger.warning("Falha gravando Firestore, caindo para JSON: %s", exc)
    _save_state_to_file(state)


def save_aula(aula: AulaItem) -> None:
    """Persiste uma única aula. Preferido sobre save_state quando só uma muda."""
    aula.updated_at = now_utc()
    if firestore_store.is_available():
        try:
            firestore_store.upsert_aula(aula)
            return
        except Exception as exc:
            logger.warning("Falha upsertando aula %s: %s", aula.id, exc)
    # Sem Firestore: cai para gravar o estado inteiro no arquivo.
    state = _load_state_from_file()
    for i, a in enumerate(state.aulas):
        if a.id == aula.id:
            state.aulas[i] = aula
            break
    else:
        state.aulas.append(aula)
    _save_state_to_file(state)


def load_aula(aula_id: str) -> Optional[AulaItem]:
    """Lê uma única aula. Em Firestore é uma leitura O(1)."""
    if firestore_store.is_available():
        try:
            return firestore_store.get_aula(aula_id)
        except Exception as exc:
            logger.warning("Falha lendo aula %s do Firestore: %s", aula_id, exc)
    state = _load_state_from_file()
    for a in state.aulas:
        if a.id == aula_id:
            return a
    return None


def _save_state_to_file(state: AulasState) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump(mode="json")
    STATE_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def synchronize_with_filesystem(state: AulasState) -> AulasState:
    """Sincroniza o estado com a árvore local de módulos/aulas.

    Em Cloud Run a árvore local existe (foi para a imagem via Dockerfile),
    então essa função detecta novas aulas. Em outros ambientes sem a
    árvore, é no-op e mantém o estado atual.
    """
    existing = {a.id: a for a in state.aulas}
    synced: list[AulaItem] = []

    if not MODULOS_ROOT.exists():
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
            ai_artifacts = prev.ai_artifacts if prev else {}

            paths = detect_aula_paths(aula_dir)
            pdf_info = compute_pdf_info(paths["artigos_dir"], prev.pdfs if prev else None)
            texto_preview = read_preview(paths["texto_aula"]) or (prev.texto_preview if prev else None)

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
                        "pptx_final": _rel_or_none(paths["pptx_final"]),
                        "pptx_web_view_link": prev.arquivos.pptx_web_view_link if prev else None,
                    },
                    texto_preview=texto_preview,
                    created_at=created_at,
                    updated_at=prev.updated_at if prev else now_utc(),
                    historico=historico,
                    drive_folder_id=drive_folder_id,
                    drive_subfolders=drive_subfolders,
                    ai_artifacts=ai_artifacts,
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

    module_prefix = aula_dir.name.split("_", 2)
    pptx_candidate = None
    if len(module_prefix) >= 2:
        pptx_candidate = aula_dir / f"{module_prefix[0]}_{module_prefix[1]}.pptx"

    return {
        "bibliografia_dir": bibliografia_dir if bibliografia_dir.exists() else None,
        "livros_extraidos_dir": livros_extraidos_dir if livros_extraidos_dir.exists() else None,
        "artigos_dir": artigos_dir if artigos_dir.exists() else None,
        "texto_aula": texto_aula if texto_aula.exists() else None,
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
    """Inicializa o estado. Em Firestore, evita gravar tudo quando já
    existem aulas — só persiste novas aulas detectadas via filesystem."""
    state = load_state()
    pre_ids = {a.id for a in state.aulas}
    state = synchronize_with_filesystem(state)

    if firestore_store.is_available():
        new_aulas = [a for a in state.aulas if a.id not in pre_ids]
        if new_aulas:
            firestore_store.upsert_many(new_aulas)
        return state

    save_state(state)
    return state


if __name__ == "__main__":
    boot = write_bootstrap_state()
    print(json.dumps({"aulas": len(boot.aulas), "updated_at": boot.updated_at.isoformat()}, ensure_ascii=False))
