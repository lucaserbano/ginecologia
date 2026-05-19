from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from schemas import NEXT_ACTION_BY_STATUS, STATUS_FLOW, STATUS_LABEL_MAP, AulaItem, StatusKey


def _set_status(aula: AulaItem, new_status: StatusKey, acao: str, mensagem: Optional[str] = None) -> AulaItem:
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
    return aula


def run_action(aula: AulaItem, action_key: str, note: Optional[str] = None) -> Tuple[AulaItem, str]:
    status = aula.status

    if action_key == "voltar_etapa":
        return _move_stage(aula, -1, action_key, note)

    if action_key == "avancar_etapa":
        return _move_stage(aula, +1, action_key, note)

    if action_key == "gerar_bibliografia":
        if status == "proximas_aulas":
            _set_status(aula, "bibliografia_em_geracao", action_key, note)
            return aula, "Bibliografia em geração (simulado)."
        return aula, _invalid(action_key, status)

    if action_key == "aprovar_bibliografia":
        if status == "bibliografia_em_geracao":
            _set_status(aula, "bibliografia_pronta", action_key, note)
            return aula, "Bibliografia marcada como pronta."
        if status == "bibliografia_pronta":
            _set_status(aula, "aguardando_aprovacao_fontes", action_key, note)
            return aula, "Aguardando aprovação final das fontes."
        if status == "aguardando_aprovacao_fontes":
            _set_status(aula, "aguardando_pdfs", action_key, note)
            return aula, "Fontes aprovadas. Aula aguardando PDFs."
        return aula, _invalid(action_key, status)

    if action_key == "marcar_pdfs":
        if status == "aguardando_pdfs":
            _set_status(aula, "pdfs_adicionados", action_key, note)
            return aula, "PDFs marcados como adicionados."
        return aula, _invalid(action_key, status)

    if action_key == "gerar_texto":
        if status == "pdfs_adicionados":
            _set_status(aula, "texto_em_producao", action_key, note)
            return aula, "Geração de texto iniciada (simulado)."
        if status == "texto_em_producao":
            _set_status(aula, "texto_pronto_revisao", action_key, note)
            return aula, "Texto pronto para revisão."
        return aula, _invalid(action_key, status)

    if action_key == "enviar_revisao":
        if status == "texto_pronto_revisao":
            _set_status(aula, "texto_revisado", action_key, note)
            return aula, "Texto revisado."
        if status == "texto_em_producao":
            _set_status(aula, "texto_pronto_revisao", action_key, note)
            return aula, "Texto enviado e marcado como pronto para revisão."
        return aula, _invalid(action_key, status)

    if action_key == "gerar_pptx":
        if status == "texto_revisado":
            _set_status(aula, "slides_em_producao", action_key, note)
            return aula, "Slides em produção (simulado)."
        if status == "slides_em_producao":
            _set_status(aula, "pptx_pronto", action_key, note)
            return aula, "PPTX pronto."
        return aula, _invalid(action_key, status)

    if action_key == "concluir":
        if status == "pptx_pronto":
            _set_status(aula, "revisao_final", action_key, note)
            return aula, "Aula em revisão final."
        if status == "revisao_final":
            _set_status(aula, "concluida", action_key, note)
            return aula, "Aula concluída."
        return aula, _invalid(action_key, status)

    if action_key == "abrir_pasta":
        # Sem transição de status.
        return aula, "Solicitação para abrir pasta enviada."

    return aula, f"Ação desconhecida: {action_key}"


def _invalid(action_key: str, status: StatusKey) -> str:
    status_label = STATUS_LABEL_MAP.get(status, status)
    return f"Ação '{action_key}' não permitida no status atual: {status_label}."


def _move_stage(aula: AulaItem, step: int, action_key: str, note: Optional[str]) -> Tuple[AulaItem, str]:
    idx = STATUS_FLOW.index(aula.status)
    nxt = idx + step
    if nxt < 0 or nxt >= len(STATUS_FLOW):
        return aula, _invalid(action_key, aula.status)
    to_status = STATUS_FLOW[nxt]
    _set_status(aula, to_status, action_key, note)
    direction = "avançou" if step > 0 else "voltou"
    return aula, f"Aula {direction} para: {STATUS_LABEL_MAP[to_status]}."
