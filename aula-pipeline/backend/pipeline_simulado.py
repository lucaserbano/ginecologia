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

    if action_key == "marcar_pdfs_baixados":
        if status == "bibliografia_pronta":
            _set_status(aula, "pdfs_baixados", action_key, note)
            return aula, "PDFs marcados como baixados."
        return aula, _invalid(action_key, status)

    if action_key == "salvar_texto_inicial":
        if status == "pdfs_baixados":
            _set_status(aula, "texto_feito", action_key, note)
            return aula, "Texto inicial salvo. Pronto para edição."
        return aula, _invalid(action_key, status)

    if action_key == "concluir_edicao":
        if status == "texto_feito":
            _set_status(aula, "texto_editado", action_key, note)
            return aula, "Edição concluída. Pronto para gerar PPTX."
        return aula, _invalid(action_key, status)

    if action_key == "marcar_imagens_prontas":
        if status == "pptx_gerado":
            _set_status(aula, "pptx_finalizado", action_key, note)
            return aula, "Imagens marcadas como prontas."
        return aula, _invalid(action_key, status)

    if action_key == "abrir_pasta":
        return aula, "Solicitação para abrir pasta enviada."

    return aula, f"Ação desconhecida: {action_key}"


def _invalid(action_key: str, status: StatusKey) -> str:
    status_label = STATUS_LABEL_MAP.get(status, status)
    return f"Ação '{action_key}' não permitida no status atual: {status_label}."


def _move_stage(aula: AulaItem, step: int, action_key: str, note: Optional[str]) -> Tuple[AulaItem, str]:
    if aula.status not in STATUS_FLOW:
        return aula, _invalid(action_key, aula.status)
    idx = STATUS_FLOW.index(aula.status)
    nxt = idx + step
    if nxt < 0 or nxt >= len(STATUS_FLOW):
        return aula, _invalid(action_key, aula.status)
    to_status = STATUS_FLOW[nxt]
    _set_status(aula, to_status, action_key, note)
    direction = "avançou" if step > 0 else "voltou"
    return aula, f"Aula {direction} para: {STATUS_LABEL_MAP[to_status]}."
