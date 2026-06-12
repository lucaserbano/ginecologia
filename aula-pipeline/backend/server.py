from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import logging
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import re
from drive_artifacts import read_markdown_file_from_drive, write_markdown_file_to_drive
from drive_client import DriveAuthError, build_drive, download_file_bytes
from drive_sync import (
    bootstrap_drive_structure,
    cleanup_duplicates_all,
    list_aula_drive_files,
    move_pptx_sem_imagens_to_prontos,
    upload_local_file_for_aula,
    upload_pptx_to_modulo_sem_imagens,
)
from pptx_builder import build_pptx, compor_referencias
from ai_actions import format_ai_error, run_ai_action_if_enabled, run_bibliografia_sync
from pipeline_simulado import run_action
from schemas import (
    ACTION_KEY_BY_ROUTE,
    NEXT_ACTION_BY_STATUS,
    STATUS_COLUMNS,
    ActionRequest,
    ActionResponse,
    AulaItem,
    AulasState,
    AdicionarLinkRequest,
    AtualizarJobRequest,
    DownloadJob,
    DriveUploadRequest,
    RemoverLinkRequest,
    TextoRequest,
    TextoResponse,
)
from settings import ALLOWED_ORIGINS, DRIVE_ROOT_FOLDER_ID, OPEN_FOLDER_ACTION_ENABLED, ensure_drive_env
from store import REPO_ROOT, load_state, save_aula, save_state, synchronize_with_filesystem, write_bootstrap_state

APP_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = APP_ROOT / "dashboard"
logger = logging.getLogger("gineco-api")

app = FastAPI(title="Kanban Aulas Gineco", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=False), name="dashboard")
else:
    logger.warning("Dashboard directory ausente em %s; endpoints de UI local desativados.", DASHBOARD_DIR)


@app.on_event("startup")
def startup_bootstrap() -> None:
    try:
        write_bootstrap_state()
    except Exception as exc:
        # Em Cloud Run, o container pode não carregar toda a árvore local do projeto.
        # Não derrubar a API por bootstrap local não disponível.
        logger.warning("Bootstrap local ignorado no startup: %s", exc)


@app.get("/")
def index() -> FileResponse:
    if not DASHBOARD_DIR.exists():
        raise HTTPException(
            status_code=404,
            detail="Dashboard local indisponível neste deploy. Use os endpoints /api/*.",
        )
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/drive/status")
def drive_status() -> dict:
    ok, message = ensure_drive_env()
    if not ok:
        return {"ok": False, "authorized": False, "message": message}
    try:
        build_drive(interactive=False)
        return {"ok": True, "authorized": True, "message": "Drive OAuth pronto."}
    except Exception as exc:
        return {"ok": False, "authorized": False, "message": str(exc)}


@app.post("/api/drive/auth-start")
def drive_auth_start() -> dict:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    try:
        build_drive(interactive=True)
        return {"ok": True, "message": "Autorização OAuth concluída e token salvo."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha na autorização OAuth: {exc}")


@app.post("/api/drive/bootstrap")
def drive_bootstrap(force_relink: bool = False, max_aulas: int = 0) -> dict:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    try:
        service = build_drive(interactive=False)
    except DriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    state = load_state()
    state = synchronize_with_filesystem(state)
    try:
        summary = bootstrap_drive_structure(
            state,
            service,
            DRIVE_ROOT_FOLDER_ID,
            force_relink=force_relink,
            max_aulas=(max_aulas if max_aulas > 0 else None),
        )
        save_state(state)
        return {"ok": True, "message": "Estrutura de pastas no Drive sincronizada.", "summary": summary}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha no bootstrap do Drive: {exc}")


@app.post("/api/drive/cleanup")
def drive_cleanup(dry_run: bool = False) -> dict:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    try:
        service = build_drive(interactive=False)
    except DriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    state = load_state()
    try:
        summary = cleanup_duplicates_all(service, state, dry_run=dry_run)
        mode = "dry-run" if dry_run else "executado"
        return {
            "ok": True,
            "message": f"Limpeza de duplicatas no Drive ({mode}).",
            "summary": summary,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha na limpeza Drive: {exc}")


@app.get("/api/columns")
def get_columns() -> dict:
    return {"columns": STATUS_COLUMNS}


@app.get("/api/aulas", response_model=AulasState)
def list_aulas() -> AulasState:
    state = load_state()
    state = synchronize_with_filesystem(state)
    return state


@app.get("/api/aulas/{aula_id}", response_model=AulaItem)
def get_aula(aula_id: str) -> AulaItem:
    state = load_state()
    state = synchronize_with_filesystem(state)
    for aula in state.aulas:
        if aula.id == aula_id:
            return aula
    raise HTTPException(status_code=404, detail="Aula não encontrada")


@app.get("/api/aulas/{aula_id}/drive-files")
def get_aula_drive_files(aula_id: str) -> dict:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    try:
        service = build_drive(interactive=False)
    except DriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    state = load_state()
    state = synchronize_with_filesystem(state)
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada. Rode /api/drive/bootstrap.")

    try:
        files = list_aula_drive_files(aula, service)
        save_aula(aula)
        return {"ok": True, "aula_id": aula.id, "count": len(files), "files": files}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao listar arquivos no Drive: {exc}")


@app.get("/api/aulas/{aula_id}/drive-files/{file_id}/download")
def download_aula_drive_file(aula_id: str, file_id: str) -> Response:
    """Streama os bytes de um arquivo do Drive da aula. Usado pelo runner local
    como fallback de ingestão no NotebookLM quando `source add-drive` falha."""
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    try:
        service = build_drive(interactive=False)
    except DriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    try:
        content = download_file_bytes(service, file_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao baixar arquivo do Drive: {exc}")
    return Response(content=content, media_type="application/pdf")


@app.post("/api/aulas/{aula_id}/upload")
def upload_aula_file(aula_id: str, payload: DriveUploadRequest) -> dict:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    try:
        service = build_drive(interactive=False)
    except DriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    state = load_state()
    state = synchronize_with_filesystem(state)
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada. Rode /api/drive/bootstrap.")

    local_path = REPO_ROOT / payload.local_relative_path
    if not local_path.exists():
        raise HTTPException(status_code=404, detail=f"Arquivo local não encontrado: {payload.local_relative_path}")

    try:
        uploaded = upload_local_file_for_aula(
            aula=aula,
            drive_service=service,
            local_path=local_path,
            target_subfolder=payload.target_subfolder,
            target_name=payload.target_name,
        )
        save_aula(aula)
        return {"ok": True, "message": "Upload concluído.", "file": uploaded}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha no upload para Drive: {exc}")


@app.post("/api/aulas/{aula_id}/upload-browser")
async def upload_aula_file_browser(
    aula_id: str,
    file: UploadFile = File(...),
    target_subfolder: Optional[str] = Form(default=None),
    target_name: Optional[str] = Form(default=None),
) -> dict:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    try:
        service = build_drive(interactive=False)
    except DriveAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    state = load_state()
    state = synchronize_with_filesystem(state)
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada. Rode /api/drive/bootstrap.")

    suffix = Path(file.filename or "upload.bin").suffix
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        uploaded = upload_local_file_for_aula(
            aula=aula,
            drive_service=service,
            local_path=tmp_path,
            target_subfolder=(target_subfolder or None),
            target_name=(target_name or None),
        )
        save_aula(aula)
        return {"ok": True, "message": "Upload via navegador concluído.", "file": uploaded}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha no upload via navegador: {exc}")
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        await file.close()


@app.post("/api/aulas/{aula_id}/actions/{action_route}", response_model=ActionResponse)
def run_aula_action(
    aula_id: str,
    action_route: str,
    background_tasks: BackgroundTasks,
    payload: Optional[ActionRequest] = None,
) -> ActionResponse:
    note = payload.note if payload else None
    state = load_state()
    state = synchronize_with_filesystem(state)

    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")

    if action_route not in ACTION_KEY_BY_ROUTE:
        raise HTTPException(status_code=404, detail="Ação não encontrada")

    action_key = ACTION_KEY_BY_ROUTE[action_route]

    if action_key == "abrir_pasta":
        ok, message = _open_folder(aula.pasta_absoluta)
        save_aula(aula)
        return ActionResponse(ok=ok, message=message, aula=aula)

    if action_key == "gerar_bibliografia":
        # Aceita 'proximas_aulas' (1ª vez) e 'bibliografia_em_geracao'
        # (retomar uma geração que travou) e 'erro_bloqueada' (retry pós-erro).
        if aula.status not in {"proximas_aulas", "bibliografia_em_geracao", "erro_bloqueada"}:
            return ActionResponse(
                ok=False,
                message="Geração de bibliografia só pode ser iniciada/retomada em 'Próximas aulas' ou 'Bibliografia em geração'.",
                aula=aula,
            )
        from datetime import datetime as _dt
        retomada = aula.status != "proximas_aulas"
        aula.historico.append(
            {
                "timestamp": _dt.utcnow(),
                "acao": "gerar_bibliografia",
                "de_status": aula.status,
                "para_status": "bibliografia_em_geracao",
                "mensagem": "Geração retomada." if retomada else "Geração iniciada.",
            }
        )
        aula.status = "bibliografia_em_geracao"
        aula.proxima_acao = NEXT_ACTION_BY_STATUS["bibliografia_em_geracao"]
        aula.progresso = "Na fila…"
        aula.pendencias = []
        aula.updated_at = _dt.utcnow()
        save_aula(aula)
        background_tasks.add_task(_run_bibliografia_background, aula_id, note)
        msg = "Geração retomada." if retomada else "Geração de bibliografia iniciada em segundo plano."
        return ActionResponse(ok=True, message=msg, aula=aula)

    if action_key == "marcar_imagens_prontas":
        # Etapa final: move o .pptx de 'pptx sem imagens' para 'pptx prontos'.
        if aula.status != "pptx_gerado":
            return ActionResponse(
                ok=False,
                message="'Imagens prontas' só é permitido a partir de 'PPTX gerado'.",
                aula=aula,
            )
        ok, message = ensure_drive_env()
        if not ok:
            return ActionResponse(ok=False, message=message, aula=aula)
        try:
            service = build_drive(interactive=False)
            moved = move_pptx_sem_imagens_to_prontos(service, aula, DRIVE_ROOT_FOLDER_ID)
        except Exception as exc:
            return ActionResponse(
                ok=False,
                message=f"Falha ao mover o PPTX para 'pptx prontos': {exc}",
                aula=aula,
            )

        aula.arquivos.pptx_web_view_link = (
            moved.get("webViewLink") or aula.arquivos.pptx_web_view_link
        )
        _force_status(
            aula,
            "pptx_finalizado",
            "marcar_imagens_prontas",
            "Imagens prontas — PPTX movido para 'pptx prontos'.",
        )
        save_aula(aula)
        return ActionResponse(
            ok=True,
            message="Imagens marcadas como prontas. PPTX movido para a pasta 'pptx prontos' do módulo.",
            aula=aula,
        )

    if action_key == "gerar_pptx":
        # Monta o .pptx real a partir do template e do texto editado no Drive.
        if aula.status != "texto_editado":
            return ActionResponse(
                ok=False,
                message="Gerar PPTX só é permitido a partir de 'Texto editado'.",
                aula=aula,
            )
        ok, message = ensure_drive_env()
        if not ok:
            return ActionResponse(ok=False, message=message, aula=aula)

        texto = (
            read_markdown_file_from_drive(aula, "04_aula_texto.md", subfolder="04_aula_texto")
            or aula.ai_artifacts.get("04_aula_texto.md")
            or aula.texto_preview
            or ""
        )
        if not texto.strip():
            return ActionResponse(
                ok=False,
                message="Texto da aula vazio no Drive — não há conteúdo para montar o PPTX.",
                aula=aula,
            )

        # Compila as referências dos 4 .md curados (subpasta 01_bibliografia)
        # para o slide final. São as fontes que o coordenador manteve.
        bib_files: dict[str, str] = {}
        for _bib in ("diretrizes_consensos.md", "pubmed_busca.md", "uptodate.md", "capitulos_livros.md"):
            _conteudo = (
                read_markdown_file_from_drive(aula, _bib, subfolder="01_bibliografia")
                or aula.ai_artifacts.get(_bib)
                or ""
            )
            if _conteudo:
                bib_files[_bib] = _conteudo
        referencias_text = compor_referencias(bib_files)

        try:
            pptx_bytes, n_slides = build_pptx(
                texto=texto,
                modulo_num=aula.modulo_num,
                modulo_nome=aula.modulo_nome,
                aula_num=aula.aula_num,
                aula_nome=aula.aula_tema,
                referencias_text=referencias_text,
            )
        except Exception as exc:
            return ActionResponse(ok=False, message=f"Falha ao montar o PPTX: {exc}", aula=aula)

        target_name = f"{aula.id}.pptx"
        tmp_path = Path(tempfile.gettempdir()) / target_name
        try:
            tmp_path.write_bytes(pptx_bytes)
            service = build_drive(interactive=False)
            uploaded = upload_pptx_to_modulo_sem_imagens(
                service, aula, DRIVE_ROOT_FOLDER_ID, tmp_path, target_name
            )
        except Exception as exc:
            return ActionResponse(ok=False, message=f"Falha ao salvar o PPTX no Drive: {exc}", aula=aula)
        finally:
            try:
                tmp_path.unlink()
            except Exception:
                pass

        aula.arquivos.pptx_web_view_link = (
            uploaded.get("webViewLink") or aula.arquivos.pptx_web_view_link
        )
        ref_nota = " — último slide: referências" if referencias_text.strip() else ""
        _force_status(
            aula,
            "pptx_gerado",
            "gerar_pptx",
            f"PPTX gerado ({n_slides} slides{ref_nota}) na pasta 'pptx sem imagens'.",
        )
        save_aula(aula)
        link = uploaded.get("webViewLink") or ""
        return ActionResponse(
            ok=True,
            message=f"PPTX gerado com {n_slides} slides{ref_nota} e salvo em 'pptx sem imagens'. {link}".strip(),
            aula=aula,
        )

    try:
        ai_handled, ai_message = run_ai_action_if_enabled(aula, action_key, note=note)
        if ai_handled:
            save_aula(aula)
            return ActionResponse(ok=True, message=ai_message, aula=aula)
    except Exception as exc:
        save_aula(aula)
        return ActionResponse(ok=False, message=format_ai_error(exc), aula=aula)

    aula, message = run_action(aula, action_key, note=note)
    save_aula(aula)

    ok = not message.startswith("Ação '")
    return ActionResponse(ok=ok, message=message, aula=aula)


def _force_status(aula: AulaItem, new_status: str, acao: str, mensagem: str) -> None:
    from datetime import datetime as _dt
    aula.historico.append(
        {
            "timestamp": _dt.utcnow(),
            "acao": acao,
            "de_status": aula.status,
            "para_status": new_status,
            "mensagem": mensagem,
        }
    )
    aula.status = new_status
    aula.proxima_acao = NEXT_ACTION_BY_STATUS.get(new_status, aula.proxima_acao)
    aula.updated_at = _dt.utcnow()


# Limita gerações simultâneas: evita estourar memória/CPU quando o usuário
# dispara várias aulas de uma vez. As demais esperam na fila.
_GENERATION_SEMAPHORE = threading.BoundedSemaphore(2)


def _run_bibliografia_background(aula_id: str, note: Optional[str]) -> None:
    from store import load_aula

    def _on_progress(msg: str) -> None:
        # Cada step relê a aula em isolado pra evitar overwrite e minimizar writes.
        a = load_aula(aula_id)
        if not a:
            return
        a.progresso = msg
        save_aula(a)

    # Espera vaga na fila (no máx. 2 gerações simultâneas).
    acquired = _GENERATION_SEMAPHORE.acquire(timeout=1800)
    if not acquired:
        a = load_aula(aula_id)
        if a:
            a.progresso = None
            a.pendencias = ["Geração não iniciou: fila cheia por tempo demais. Tente retomar."]
            _force_status(a, "erro_bloqueada", "gerar_bibliografia", "Timeout na fila.")
            save_aula(a)
        return

    try:
        aula = load_aula(aula_id)
        if not aula:
            return
        run_bibliografia_sync(aula, note, on_progress=_on_progress)
        save_aula(aula)
    except Exception as exc:
        a = load_aula(aula_id)
        if a:
            a.progresso = None
            a.pendencias = [f"Falha na geração da bibliografia: {exc}"]
            _force_status(a, "erro_bloqueada", "gerar_bibliografia", f"Erro: {exc}")
            save_aula(a)
    finally:
        _GENERATION_SEMAPHORE.release()


# ---------------------------------------------------------------------------
# Endpoints de texto (04_aula_texto.md no Drive)
# ---------------------------------------------------------------------------


@app.get("/api/aulas/{aula_id}/texto", response_model=TextoResponse)
def get_aula_texto(aula_id: str) -> TextoResponse:
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        return TextoResponse(ok=True, conteudo="", fonte="vazio")
    conteudo = read_markdown_file_from_drive(aula, "04_aula_texto.md", subfolder="04_aula_texto")
    if conteudo:
        return TextoResponse(ok=True, conteudo=conteudo, fonte="drive")
    return TextoResponse(ok=True, conteudo="", fonte="vazio")


@app.put("/api/aulas/{aula_id}/texto", response_model=TextoResponse)
def put_aula_texto(
    aula_id: str,
    payload: TextoRequest,
    background_tasks: BackgroundTasks,
) -> TextoResponse:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)

    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada. Rode /api/drive/bootstrap.")

    # Resposta otimista: atualiza estado e dispara upload Drive em background.
    aula.texto_preview = (payload.conteudo or "")[:420].strip() or None
    # Remove pendencia anterior, se houver.
    aula.pendencias = [p for p in aula.pendencias if not p.startswith("Falha ao salvar texto no Drive")]
    save_aula(aula)

    background_tasks.add_task(
        _write_texto_drive_bg,
        aula_id=aula_id,
        conteudo=payload.conteudo,
    )
    return TextoResponse(ok=True, conteudo=payload.conteudo, fonte="drive")


def _write_texto_drive_bg(aula_id: str, conteudo: str) -> None:
    from store import load_aula
    aula = load_aula(aula_id)
    if not aula:
        return
    try:
        write_markdown_file_to_drive(
            aula=aula,
            filename="04_aula_texto.md",
            content=conteudo,
            subfolder="04_aula_texto",
        )
    except Exception as exc:
        a = load_aula(aula_id) or aula
        a.pendencias = list(dict.fromkeys(a.pendencias + [f"Falha ao salvar texto no Drive: {exc}"]))
        save_aula(a)


BIBLIOGRAFIA_FILES = [
    "01_bibliografia.md",
    "diretrizes_consensos.md",
    "pubmed_busca.md",
    "uptodate.md",
    "capitulos_livros.md",
]


@app.post("/api/aulas/{aula_id}/rehidratar-bibliografia")
def rehidratar_bibliografia(aula_id: str) -> dict:
    """Lê os .md de bibliografia do Drive (subpasta 01_bibliografia) e
    popula `aula.ai_artifacts`. Útil quando o container reinicia e o
    estado in-memory perde os artifacts mas os arquivos seguem no Drive."""
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada.")

    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)

    carregados = []
    for filename in BIBLIOGRAFIA_FILES:
        conteudo = read_markdown_file_from_drive(aula, filename, subfolder="01_bibliografia")
        if conteudo:
            aula.ai_artifacts[filename] = conteudo
            carregados.append(filename)

    save_aula(aula)
    return {
        "ok": True,
        "aula_id": aula.id,
        "carregados": carregados,
        "total": len(carregados),
    }


# ---------------------------------------------------------------------------
# Bibliografia: remover um link de uma fonte (.md)
# ---------------------------------------------------------------------------


@app.post("/api/aulas/{aula_id}/bibliografia/remover-link")
def remover_link_bibliografia(
    aula_id: str,
    payload: RemoverLinkRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")

    source = payload.source
    target_url = (payload.url or "").strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="URL vazia.")

    # Fonte: estado interno tem prioridade; se não, tenta Drive.
    conteudo = aula.ai_artifacts.get(source) or ""
    if not conteudo:
        conteudo = read_markdown_file_from_drive(aula, source, subfolder="01_bibliografia")
    if not conteudo:
        raise HTTPException(status_code=404, detail=f"Arquivo {source} não encontrado para esta aula.")

    novo_conteudo, removidos = _remove_link_from_markdown(conteudo, target_url)
    if removidos == 0:
        raise HTTPException(status_code=404, detail="Link não encontrado no arquivo.")

    # Atualização otimista: já grava no estado. O upload Drive vai em background.
    aula.ai_artifacts[source] = novo_conteudo
    aula.pendencias = [p for p in aula.pendencias if not p.startswith(f"Falha ao atualizar {source}")]
    save_aula(aula)

    drive_scheduled = False
    if aula.drive_folder_id:
        ok, _msg = ensure_drive_env()
        if ok:
            background_tasks.add_task(
                _write_bibliografia_drive_bg,
                aula_id=aula_id,
                filename=source,
                conteudo=novo_conteudo,
            )
            drive_scheduled = True

    return {
        "ok": True,
        "removidos": removidos,
        "drive_scheduled": drive_scheduled,
        "source": source,
    }


@app.post("/api/aulas/{aula_id}/bibliografia/adicionar-link")
def adicionar_link_bibliografia(
    aula_id: str,
    payload: AdicionarLinkRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Acrescenta manualmente uma referência a uma das fontes curadas (.md).
    Espelha `remover_link_bibliografia`: atualização otimista no estado +
    gravação no Drive em background. Idempotente: não duplica URL existente."""
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")

    source = payload.source
    target_url = (payload.url or "").strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="URL vazia.")

    # Fonte: estado interno tem prioridade; se não, tenta Drive. Pode não
    # existir ainda (aula sem aquele arquivo) — nesse caso começa vazio.
    conteudo = aula.ai_artifacts.get(source) or ""
    if not conteudo:
        conteudo = read_markdown_file_from_drive(aula, source, subfolder="01_bibliografia") or ""

    novo_conteudo, added = _append_link_to_markdown(
        conteudo, target_url, payload.titulo, payload.meta
    )
    if not added:
        return {"ok": True, "added": False, "source": source}

    # Atualização otimista: já grava no estado. O upload Drive vai em background.
    aula.ai_artifacts[source] = novo_conteudo
    aula.pendencias = [p for p in aula.pendencias if not p.startswith(f"Falha ao atualizar {source}")]
    save_aula(aula)

    drive_scheduled = False
    if aula.drive_folder_id:
        ok, _msg = ensure_drive_env()
        if ok:
            background_tasks.add_task(
                _write_bibliografia_drive_bg,
                aula_id=aula_id,
                filename=source,
                conteudo=novo_conteudo,
            )
            drive_scheduled = True

    return {
        "ok": True,
        "added": True,
        "drive_scheduled": drive_scheduled,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Fase 1: links estruturados + job de download de PDFs (runner local)
# ---------------------------------------------------------------------------

# Fontes de referências varridas para o download (capitulos_livros fica de
# fora: são capítulos já extraídos para 02_livros_extraidos no Drive).
LINK_SOURCES = [
    "diretrizes_consensos.md",
    "pubmed_busca.md",
    "uptodate.md",
    "capitulos_livros.md",
]

_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")


def _classify_link_kind(url: str) -> str:
    """Classifica um link para guiar o download automático do runner."""
    u = url.lower()
    after_scheme = u.split("://", 1)[-1]
    host = after_scheme.split("/", 1)[0]
    path = after_scheme[len(host):]
    if "uptodate.com" in host:
        return "uptodate"
    if "drive.google.com" in host or "docs.google.com" in host:
        return "drive"
    # PMC (open-access) é baixável direto.
    if "pmc.ncbi.nlm.nih.gov" in host or ("ncbi.nlm.nih.gov" in host and "/pmc/" in path):
        return "pmc"
    if "pubmed.ncbi.nlm.nih.gov" in host:
        return "pubmed"
    # URL que aponta para um .pdf (ignorando query).
    if path.split("?", 1)[0].rstrip("/").endswith(".pdf"):
        return "pdf_direto"
    return "outro"


def _extract_links_from_markdown(md: str, source: str) -> list[dict]:
    """Extrai [{source, title, url, meta, kind}] de um markdown de bibliografia.
    Espelha a regex usada no dashboard (extractLinksFromMarkdown)."""
    if not md:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for m in _MD_LINK_RE.finditer(md):
        title = (m.group(1) or "").strip()
        url = (m.group(2) or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        tail = md[m.end():m.end() + 240]
        meta = ""
        # Só captura meta na MESMA linha do link ([ \t]* não cruza newline),
        # para não absorver o bullet da linha seguinte.
        meta_match = re.match(r"[ \t]*[—·\-]\s*([^\n]+)", tail)
        if meta_match:
            meta = meta_match.group(1).strip()
        out.append(
            {
                "source": source,
                "title": title,
                "url": url,
                "meta": meta,
                "kind": _classify_link_kind(url),
            }
        )
    return out


def _collect_aula_links(aula: AulaItem) -> list[dict]:
    """Junta os links de todas as fontes curadas da aula (estado interno
    primeiro; cai para o Drive quando o cache está vazio)."""
    links: list[dict] = []
    seen: set[str] = set()
    for source in LINK_SOURCES:
        conteudo = aula.ai_artifacts.get(source) or ""
        if not conteudo:
            conteudo = read_markdown_file_from_drive(aula, source, subfolder="01_bibliografia") or ""
        for link in _extract_links_from_markdown(conteudo, source):
            if link["url"] in seen:
                continue
            seen.add(link["url"])
            links.append(link)
    return links


@app.get("/api/aulas/{aula_id}/links")
def listar_links_aula(aula_id: str) -> dict:
    """Lista estruturada de todas as referências da aula, com `kind` para
    o runner decidir como baixar cada uma."""
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    links = _collect_aula_links(aula)
    return {"ok": True, "aula_id": aula.id, "total": len(links), "links": links}


@app.post("/api/aulas/{aula_id}/job/download-pdfs")
def enfileirar_download_pdfs(aula_id: str) -> dict:
    """Enfileira um job de download de PDFs para o runner local processar.
    Não mexe no `status` da aula — o job é ortogonal ao state machine."""
    from datetime import datetime as _dt
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada.")

    if aula.job and aula.job.status in {"pendente", "em_andamento"}:
        return {"ok": True, "ja_em_andamento": True, "job": aula.job.model_dump(mode="json")}

    agora = _dt.utcnow()
    aula.job = DownloadJob(status="pendente", criado_em=agora, atualizado_em=agora)
    save_aula(aula)
    return {"ok": True, "ja_em_andamento": False, "job": aula.job.model_dump(mode="json")}


@app.post("/api/aulas/{aula_id}/job/gerar-texto-notebooklm")
def enfileirar_gerar_texto_notebooklm(aula_id: str) -> dict:
    """Enfileira um job para o runner local gerar o roteiro no NotebookLM
    (cria o notebook, sobe as fontes do Drive, roda o prompt e cola o texto).
    O job é ortogonal ao `status`; o runner avança para `texto_feito` ao colar."""
    from datetime import datetime as _dt
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada.")
    if aula.status != "pdfs_baixados":
        raise HTTPException(
            status_code=400,
            detail="Só é possível gerar o texto do NotebookLM na etapa 'PDFs baixados'.",
        )

    if aula.job and aula.job.status in {"pendente", "em_andamento"}:
        return {"ok": True, "ja_em_andamento": True, "job": aula.job.model_dump(mode="json")}

    agora = _dt.utcnow()
    aula.job = DownloadJob(
        tipo="gerar_texto_notebooklm", status="pendente", criado_em=agora, atualizado_em=agora
    )
    save_aula(aula)
    return {"ok": True, "ja_em_andamento": False, "job": aula.job.model_dump(mode="json")}


@app.put("/api/aulas/{aula_id}/job")
def atualizar_job_download(aula_id: str, payload: AtualizarJobRequest) -> dict:
    """Usado pelo runner local para reportar progresso/resultado do job."""
    from datetime import datetime as _dt
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.job:
        raise HTTPException(status_code=404, detail="Nenhum job para esta aula.")

    aula.job.status = payload.status
    aula.job.atualizado_em = _dt.utcnow()
    if payload.mensagem is not None:
        aula.job.mensagem = payload.mensagem
    if payload.baixados is not None:
        aula.job.baixados = payload.baixados
    if payload.pendentes_manuais is not None:
        aula.job.pendentes_manuais = payload.pendentes_manuais
    save_aula(aula)
    return {"ok": True, "job": aula.job.model_dump(mode="json")}


@app.get("/api/jobs/pendentes")
def listar_jobs_pendentes() -> dict:
    """Polling leve para o runner: só as aulas com job pendente/em andamento,
    com os campos mínimos que o runner precisa."""
    state = load_state()
    pend = []
    for aula in state.aulas:
        if aula.job and aula.job.status in {"pendente", "em_andamento"}:
            pend.append(
                {
                    "aula_id": aula.id,
                    "tipo": aula.job.tipo,
                    "status": aula.job.status,
                    "drive_folder_id": aula.drive_folder_id,
                }
            )
    return {"ok": True, "total": len(pend), "jobs": pend}


def _write_bibliografia_drive_bg(aula_id: str, filename: str, conteudo: str) -> None:
    from store import load_aula
    aula = load_aula(aula_id)
    if not aula:
        return
    try:
        write_markdown_file_to_drive(
            aula=aula,
            filename=filename,
            content=conteudo,
            subfolder="01_bibliografia",
        )
    except Exception as exc:
        a = load_aula(aula_id) or aula
        a.pendencias = list(dict.fromkeys(a.pendencias + [f"Falha ao atualizar {filename} no Drive: {exc}"]))
        save_aula(a)


def _remove_link_from_markdown(content: str, target_url: str) -> tuple[str, int]:
    """Remove de `content` qualquer linha (e suas continuações de bullet)
    que contenha exatamente `target_url` como link Markdown ou URL bruta.
    Retorna o conteúdo novo e o número de blocos removidos."""
    # Normaliza para comparação (sem fragment).
    needle = target_url.split("#")[0].rstrip("/")
    lines = content.splitlines()
    out: list[str] = []
    skip_continuation = False
    removed = 0
    i = 0
    bullet_re = re.compile(r"^\s*([-*+]|\d+\.)\s+")
    while i < len(lines):
        line = lines[i]
        line_urls = re.findall(r"https?://[^\s)>\]]+", line)
        match = any(u.split("#")[0].rstrip("/") == needle for u in line_urls)
        if match and bullet_re.match(line):
            # Pula linha do bullet + linhas seguintes que sejam continuação (recuo > primeira).
            removed += 1
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    break
                if bullet_re.match(nxt):
                    break
                # Continuação indentada — pula.
                if nxt.startswith(("  ", "\t")):
                    i += 1
                    continue
                break
            continue
        out.append(line)
        i += 1
    novo = "\n".join(out)
    # Preserva newline final se original tinha.
    if content.endswith("\n") and not novo.endswith("\n"):
        novo += "\n"
    return novo, removed


def _append_link_to_markdown(
    content: str,
    target_url: str,
    titulo: Optional[str],
    meta: Optional[str],
) -> tuple[str, bool]:
    """Acrescenta uma linha de bullet Markdown `- [titulo](url) — meta` ao
    final de `content`. Idempotente: se `target_url` já existir (mesma
    comparação de `_remove_link_from_markdown`), retorna sem alterar.
    Retorna (conteúdo novo, adicionou)."""
    needle = target_url.split("#")[0].rstrip("/")
    existing_urls = re.findall(r"https?://[^\s)>\]]+", content)
    if any(u.split("#")[0].rstrip("/") == needle for u in existing_urls):
        return content, False

    titulo_fmt = (titulo or "").strip() or target_url
    linha = f"- [{titulo_fmt}]({target_url})"
    meta_fmt = (meta or "").strip()
    if meta_fmt:
        linha += f" — {meta_fmt}"

    base = content.rstrip("\n")
    if base:
        novo = f"{base}\n{linha}\n"
    else:
        novo = f"{linha}\n"
    return novo, True


def _find_aula(state: AulasState, aula_id: str) -> Optional[AulaItem]:
    for aula in state.aulas:
        if aula.id == aula_id:
            return aula
    return None


def _open_folder(path_str: str) -> tuple[bool, str]:
    if not OPEN_FOLDER_ACTION_ENABLED:
        return False, "Ação 'abrir pasta' desativada neste ambiente."

    path = Path(path_str)
    if not path.exists():
        return False, "Pasta da aula não existe mais no filesystem."

    try:
        if sys.platform == "darwin":
            result = subprocess.run(["open", str(path)], check=False, capture_output=True, text=True)
        elif os.name == "nt":
            result = subprocess.run(["explorer", str(path)], check=False, capture_output=True, text=True)
        else:
            result = subprocess.run(["xdg-open", str(path)], check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return True, "Pasta aberta no sistema."
        stderr = (result.stderr or "").strip()
        return False, f"Falha ao abrir pasta: {stderr or 'erro desconhecido'}"
    except Exception as exc:
        return False, f"Falha ao abrir pasta: {exc}"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8787, reload=True)
