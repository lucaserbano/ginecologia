from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from artifact_store import format_artifact_result, persist_ai_artifact
from drive_artifacts import read_markdown_file_from_drive
from openrouter_client import OpenRouterError, generate_text
from phase1_bibliografia import format_phase1_result, run_phase1_bibliografia
from prompt_loader import load_agent_prompts
from schemas import NEXT_ACTION_BY_STATUS, AulaItem
from settings import ENABLE_AI_ACTIONS


def run_ai_action_if_enabled(aula: AulaItem, action_key: str, note: Optional[str]) -> Tuple[bool, str]:
    """Roda ações IA síncronas curtas. A geração de bibliografia agora é
    disparada de forma assíncrona em `server.py` via BackgroundTasks, então
    não é tratada aqui."""
    if not ENABLE_AI_ACTIONS:
        return False, "IA desativada por configuração."

    if action_key == "gerar_pptx":
        if aula.status != "texto_editado":
            return False, "Ação IA não aplicável no status atual."
        source = _load_texto_source(aula)
        output = _generate_outline_slides(aula, source, note)
        artifact = persist_ai_artifact(aula, "05_outline_slides.md", output)
        _set_status(aula, "pptx_gerado", action_key, "Outline de slides gerado por IA.")
        return True, f"Outline de slides gerado. {format_artifact_result(artifact)}"

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


def _generate_outline_slides(aula: AulaItem, texto: str, note: Optional[str]) -> str:
    agent_prompt = load_agent_prompts("montador-pptx.md")
    sys = (
        "Você é especialista em estrutura de aulas e storytelling de slides para medicina. "
        "Nesta etapa, gere apenas o outline em Markdown; não gere um arquivo PPTX ainda.\n\n"
        f"{agent_prompt}"
    )
    user = f"""
Monte um outline de slides para a aula:
- Aula: {aula.id}
- Tema: {aula.aula_tema}
- Observação opcional do usuário: {note or "nenhuma"}

Base textual:
{texto[:12000]}

Formato:
# Outline de Slides - {aula.id}
Liste de 12 a 20 slides:
- Título do slide
- Mensagem central (1 frase)
- Sugestão visual (gráfico, tabela, fluxograma, imagem clínica etc.)
"""
    return generate_text(sys, user, temperature=0.3, max_tokens=2200)


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_ai_error(exc: Exception) -> str:
    if isinstance(exc, OpenRouterError):
        return f"Falha OpenRouter: {exc}"
    return f"Falha IA: {exc}"


def _load_texto_source(aula: AulaItem) -> str:
    from_drive = read_markdown_file_from_drive(aula, "04_aula_texto.md", subfolder="04_aula_texto")
    if from_drive:
        return from_drive
    return aula.ai_artifacts.get("04_aula_texto.md", aula.texto_preview or "")
