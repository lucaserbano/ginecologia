from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from openrouter_client import OpenRouterError
from phase1_bibliografia import format_phase1_result, run_phase1_bibliografia
from schemas import NEXT_ACTION_BY_STATUS, AulaItem
from settings import ENABLE_AI_ACTIONS


def run_ai_action_if_enabled(aula: AulaItem, action_key: str, note: Optional[str]) -> Tuple[bool, str]:
    """Ações IA síncronas curtas.

    A geração de bibliografia roda de forma assíncrona em `server.py` via
    BackgroundTasks. A geração de PPTX virou montagem real do `.pptx`
    (`pptx_builder`), também tratada diretamente em `server.py`. Hoje
    nenhuma ação IA síncrona é processada aqui."""
    if not ENABLE_AI_ACTIONS:
        return False, "IA desativada por configuração."
    return False, "Ação não suportada pelo executor IA."


def run_bibliografia_sync(
    aula: AulaItem,
    note: Optional[str],
    on_progress=None,
) -> str:
    """Roda a Fase 1 e retorna mensagem de sucesso. Atualiza status para
    `bibliografia_pronta`. Pensado para rodar dentro de um BackgroundTask."""
    phase1 = run_phase1_bibliografia(aula, note, on_progress=on_progress)
    _set_status(aula, "bibliografia_pronta", "gerar_bibliografia", "Bibliografia fase 1 gerada.")
    aula.progresso = None
    return f"Bibliografia gerada. {format_phase1_result(phase1)}"


def _set_status(aula: AulaItem, new_status: str, acao: str, mensagem: Optional[str] = None) -> None:
    old_status = aula.status
    aula.status = new_status
    aula.proxima_acao = NEXT_ACTION_BY_STATUS[new_status]
    aula.updated_at = datetime.utcnow()
    aula.historico.append(
        {
            "timestamp": aula.updated_at,
            "acao": acao,
            "de_status": old_status,
            "para_status": new_status,
            "mensagem": mensagem,
        }
    )


def format_ai_error(exc: Exception) -> str:
    if isinstance(exc, OpenRouterError):
        return f"Falha OpenRouter: {exc}"
    return f"Falha IA: {exc}"
