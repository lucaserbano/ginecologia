from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from artifact_store import format_artifact_result, persist_ai_artifact
from openrouter_client import OpenRouterError, generate_text
from phase1_bibliografia import format_phase1_result, run_phase1_bibliografia
from prompt_loader import load_agent_prompts, load_template
from schemas import NEXT_ACTION_BY_STATUS, AulaItem
from settings import ENABLE_AI_ACTIONS


def run_ai_action_if_enabled(aula: AulaItem, action_key: str, note: Optional[str]) -> Tuple[bool, str]:
    if not ENABLE_AI_ACTIONS:
        return False, "IA desativada por configuração."

    if action_key == "gerar_bibliografia":
        if aula.status != "proximas_aulas":
            return False, "Ação IA não aplicável no status atual."
        phase1 = run_phase1_bibliografia(aula, note)
        _set_status(aula, "bibliografia_pronta", action_key, "Bibliografia fase 1 gerada.")
        return True, f"Bibliografia fase 1 gerada e marcada como pronta. {format_phase1_result(phase1)}"

    if action_key == "gerar_texto":
        if aula.status not in {"pdfs_adicionados", "texto_em_producao"}:
            return False, "Ação IA não aplicável no status atual."
        output = _generate_aula_texto(aula, note)
        artifact = persist_ai_artifact(aula, "04_aula_texto.md", output)
        aula.texto_preview = _truncate(output, 420)
        _set_status(aula, "texto_pronto_revisao", action_key, "Texto gerado por IA.")
        return True, f"Texto da aula gerado por IA e enviado para revisão. {format_artifact_result(artifact)}"

    if action_key == "enviar_revisao":
        if aula.status not in {"texto_pronto_revisao", "texto_em_producao"}:
            return False, "Ação IA não aplicável no status atual."
        source = aula.ai_artifacts.get("04_aula_texto.md", aula.texto_preview or "")
        output = _generate_revisao(aula, source, note)
        artifact = persist_ai_artifact(aula, "06_revisao.md", output)
        aula.texto_preview = _truncate(output, 420)
        _set_status(aula, "texto_revisado", action_key, "Revisão gerada por IA.")
        return True, f"Revisão científica gerada por IA. {format_artifact_result(artifact)}"

    if action_key == "gerar_pptx":
        if aula.status not in {"texto_revisado", "slides_em_producao"}:
            return False, "Ação IA não aplicável no status atual."
        source = aula.ai_artifacts.get("06_revisao.md") or aula.ai_artifacts.get("04_aula_texto.md") or ""
        output = _generate_outline_slides(aula, source, note)
        artifact = persist_ai_artifact(aula, "05_outline_slides.md", output)
        _set_status(aula, "pptx_pronto", action_key, "Outline de slides gerado por IA.")
        return True, f"Outline de slides gerado por IA e PPTX marcado como pronto. {format_artifact_result(artifact)}"

    return False, "Ação não suportada pelo executor IA."


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


def _generate_bibliografia(aula: AulaItem, note: Optional[str]) -> str:
    agent_prompt = load_agent_prompts(
        "curador-diretrizes-consensos.md",
        "buscador-pubmed.md",
        "curador-uptodate.md",
    )
    sys = (
        "Você é um curador de bibliografia médica em ginecologia. "
        "Responda em português, com precisão e sem inventar DOI/PMID se não tiver certeza.\n\n"
        f"{agent_prompt}"
    )
    user = f"""
Gere a bibliografia inicial da aula abaixo em Markdown:
- Aula: {aula.id}
- Módulo: M{aula.modulo_num} - {aula.modulo_nome}
- Tema: {aula.aula_tema}
- Observação opcional do usuário: {note or "nenhuma"}

Formato obrigatório:
# Bibliografia Inicial - {aula.id}
## Objetivo clínico
## Referências essenciais (6 a 10)
- Título | Tipo de fonte | Justificativa clínica | Link/DOI/PMID (se disponível)
## Lacunas e dúvidas para validação humana
"""
    return generate_text(sys, user, temperature=0.2, max_tokens=1800)


def _generate_aula_texto(aula: AulaItem, note: Optional[str]) -> str:
    agent_prompt = load_agent_prompts("redator-aula.md")
    system_prompt = load_template("system_prompt_aula.md")
    briefing = load_template("briefing_generico.yaml")
    sys = (
        "Você é redator médico para aulas de ginecologia. "
        "Produza conteúdo didático, objetivo e aplicável à prática clínica.\n\n"
        f"{system_prompt}\n\n{agent_prompt}"
    )
    bib = aula.ai_artifacts.get("01_bibliografia.md", "")
    user = f"""
Com base na bibliografia e contexto, redija o texto da aula em português:
- Aula: {aula.id}
- Tema: {aula.aula_tema}
- Observação opcional do usuário: {note or "nenhuma"}

Bibliografia disponível:
{bib[:10000]}

Briefing padrão:
{briefing[:6000]}

Formato obrigatório:
# Aula {aula.id} - {aula.aula_tema}
Escreva em blocos separados por '---' (cada bloco representa um slide).
Evite bullets; prefira parágrafos curtos.
Inclua sinalização de evidência fraca/controversa quando aplicável.
Para esta execução automatizada, trate a observação opcional do usuário como o checkpoint de instruções adicionais.
"""
    return generate_text(sys, user, temperature=0.25, max_tokens=2800)


def _generate_revisao(aula: AulaItem, texto: str, note: Optional[str]) -> str:
    agent_prompt = load_agent_prompts("revisor-cientifico.md")
    criterios = load_template("criterios_revisao.md")
    sys = (
        "Você é revisor científico de conteúdo médico. "
        "Revise clareza, coerência clínica e segurança de conduta.\n\n"
        f"{agent_prompt}\n\nCritérios de revisão:\n{criterios}"
    )
    user = f"""
Revise o texto da aula abaixo:
- Aula: {aula.id}
- Tema: {aula.aula_tema}
- Observação opcional do usuário: {note or "nenhuma"}

Texto para revisão:
{texto[:14000]}

Formato:
# Revisão científica - {aula.id}
## Correções críticas
## Ajustes recomendados
## Texto revisado (versão final)
"""
    return generate_text(sys, user, temperature=0.2, max_tokens=2600)


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
