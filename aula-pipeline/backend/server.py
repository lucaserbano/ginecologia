from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import logging
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from drive_artifacts import read_markdown_file_from_drive, write_markdown_file_to_drive
from drive_client import DriveAuthError, build_drive
from drive_sync import (
    bootstrap_drive_structure,
    cleanup_duplicates_all,
    find_pptx_in_aula_folder,
    list_aula_drive_files,
    move_pptx_to_modulo_final,
    upload_local_file_for_aula,
)
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
    DriveUploadRequest,
    TextoRequest,
    TextoResponse,
)
from settings import ALLOWED_ORIGINS, DRIVE_ROOT_FOLDER_ID, OPEN_FOLDER_ACTION_ENABLED, ensure_drive_env
from store import REPO_ROOT, load_state, save_state, synchronize_with_filesystem, write_bootstrap_state

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
    save_state(state)
    return state


@app.get("/api/aulas/{aula_id}", response_model=AulaItem)
def get_aula(aula_id: str) -> AulaItem:
    state = load_state()
    state = synchronize_with_filesystem(state)
    for aula in state.aulas:
        if aula.id == aula_id:
            save_state(state)
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
        save_state(state)
        return {"ok": True, "aula_id": aula.id, "count": len(files), "files": files}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao listar arquivos no Drive: {exc}")


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
        save_state(state)
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
        save_state(state)
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
        save_state(state)
        return ActionResponse(ok=ok, message=message, aula=aula)

    if action_key == "gerar_bibliografia":
        if aula.status != "proximas_aulas":
            return ActionResponse(
                ok=False,
                message="Geração de bibliografia só pode ser iniciada em 'Próximas aulas'.",
                aula=aula,
            )
        # Flip imediato para coluna 2 + dispara worker em background.
        from datetime import datetime as _dt
        aula.historico.append(
            {
                "timestamp": _dt.utcnow(),
                "acao": "gerar_bibliografia",
                "de_status": aula.status,
                "para_status": "bibliografia_em_geracao",
                "mensagem": "Geração iniciada.",
            }
        )
        aula.status = "bibliografia_em_geracao"
        aula.proxima_acao = NEXT_ACTION_BY_STATUS["bibliografia_em_geracao"]
        aula.progresso = "Iniciando…"
        aula.pendencias = []
        aula.updated_at = _dt.utcnow()
        save_state(state)
        background_tasks.add_task(_run_bibliografia_background, aula_id, note)
        return ActionResponse(ok=True, message="Geração de bibliografia iniciada em segundo plano.", aula=aula)

    if action_key == "mover_pptx_final":
        if aula.status != "pptx_finalizado":
            return ActionResponse(
                ok=False,
                message="Mover para pasta final só é permitido a partir de 'PPTX finalizado'.",
                aula=aula,
            )
        ok, message = ensure_drive_env()
        if not ok:
            return ActionResponse(ok=False, message=message, aula=aula)
        try:
            service = build_drive(interactive=False)
            moved = move_pptx_to_modulo_final(service, aula, DRIVE_ROOT_FOLDER_ID)
        except Exception as exc:
            return ActionResponse(ok=False, message=f"Falha ao mover PPTX: {exc}", aula=aula)

        aula.arquivos.pptx_web_view_link = moved.get("webViewLink") or aula.arquivos.pptx_web_view_link
        _, message = run_action_via_simulado(aula, "avancar_etapa", note=note)
        # avancar_etapa pode ter pulado se status já fora de fluxo; forçamos:
        if aula.status != "pptx_na_pasta_final":
            _force_status(aula, "pptx_na_pasta_final", "mover_pptx_final", "PPTX movido para pasta final.")
        save_state(state)
        return ActionResponse(ok=True, message="PPTX movido para a pasta final do módulo.", aula=aula)

    try:
        ai_handled, ai_message = run_ai_action_if_enabled(aula, action_key, note=note)
        if ai_handled:
            save_state(state)
            return ActionResponse(ok=True, message=ai_message, aula=aula)
    except Exception as exc:
        save_state(state)
        return ActionResponse(ok=False, message=format_ai_error(exc), aula=aula)

    aula, message = run_action(aula, action_key, note=note)
    save_state(state)

    ok = not message.startswith("Ação '")
    return ActionResponse(ok=ok, message=message, aula=aula)


def run_action_via_simulado(aula: AulaItem, action_key: str, note: Optional[str]) -> tuple[AulaItem, str]:
    return run_action(aula, action_key, note=note)


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


def _run_bibliografia_background(aula_id: str, note: Optional[str]) -> None:
    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        return

    def _on_progress(msg: str) -> None:
        # Cada step relê estado para evitar overwrite por outras ações.
        st = load_state()
        a = _find_aula(st, aula_id)
        if not a:
            return
        a.progresso = msg
        save_state(st)

    try:
        run_bibliografia_sync(aula, note, on_progress=_on_progress)
        save_state(state)
    except Exception as exc:
        # Reaquire e marca erro para evitar perder logs intermediários.
        st = load_state()
        a = _find_aula(st, aula_id)
        if not a:
            return
        a.progresso = None
        a.pendencias = [f"Falha na geração da bibliografia: {exc}"]
        _force_status(a, "erro_bloqueada", "gerar_bibliografia", f"Erro: {exc}")
        save_state(st)


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
def put_aula_texto(aula_id: str, payload: TextoRequest) -> TextoResponse:
    ok, message = ensure_drive_env()
    if not ok:
        raise HTTPException(status_code=400, detail=message)

    state = load_state()
    aula = _find_aula(state, aula_id)
    if not aula:
        raise HTTPException(status_code=404, detail="Aula não encontrada")
    if not aula.drive_folder_id:
        raise HTTPException(status_code=400, detail="Aula sem pasta Drive vinculada. Rode /api/drive/bootstrap.")

    try:
        write_markdown_file_to_drive(
            aula=aula,
            filename="04_aula_texto.md",
            content=payload.conteudo,
            subfolder="04_aula_texto",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar texto no Drive: {exc}")

    aula.texto_preview = (payload.conteudo or "")[:420].strip() or None
    save_state(state)
    return TextoResponse(ok=True, conteudo=payload.conteudo, fonte="drive")


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
