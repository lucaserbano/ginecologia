from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


StatusKey = Literal[
    "proximas_aulas",
    "bibliografia_em_geracao",
    "bibliografia_pronta",
    "aguardando_aprovacao_fontes",
    "aguardando_pdfs",
    "pdfs_adicionados",
    "texto_em_producao",
    "texto_pronto_revisao",
    "texto_revisado",
    "slides_em_producao",
    "pptx_pronto",
    "revisao_final",
    "concluida",
    "erro_bloqueada",
]

ACTION_KEY_BY_ROUTE = {
    "gerar-bibliografia": "gerar_bibliografia",
    "aprovar-bibliografia": "aprovar_bibliografia",
    "marcar-pdfs": "marcar_pdfs",
    "gerar-texto": "gerar_texto",
    "enviar-revisao": "enviar_revisao",
    "gerar-pptx": "gerar_pptx",
    "concluir": "concluir",
    "abrir-pasta": "abrir_pasta",
    "avancar-etapa": "avancar_etapa",
    "voltar-etapa": "voltar_etapa",
}

STATUS_COLUMNS: list[tuple[StatusKey, str]] = [
    ("proximas_aulas", "Próximas aulas"),
    ("bibliografia_em_geracao", "Bibliografia em geração"),
    ("bibliografia_pronta", "Bibliografia pronta"),
    ("aguardando_aprovacao_fontes", "Aguardando aprovação das fontes"),
    ("aguardando_pdfs", "Aguardando PDFs"),
    ("pdfs_adicionados", "PDFs adicionados"),
    ("texto_em_producao", "Texto em produção"),
    ("texto_pronto_revisao", "Texto pronto para revisão"),
    ("texto_revisado", "Texto revisado"),
    ("slides_em_producao", "Slides em produção"),
    ("pptx_pronto", "PPTX pronto"),
    ("revisao_final", "Revisão final"),
    ("concluida", "Concluída"),
    ("erro_bloqueada", "Erro / bloqueada"),
]

STATUS_LABEL_MAP = {k: v for k, v in STATUS_COLUMNS}
STATUS_FLOW: list[StatusKey] = [k for k, _ in STATUS_COLUMNS]

NEXT_ACTION_BY_STATUS: dict[StatusKey, str] = {
    "proximas_aulas": "Gerar bibliografia",
    "bibliografia_em_geracao": "Aprovar bibliografia",
    "bibliografia_pronta": "Aprovar bibliografia",
    "aguardando_aprovacao_fontes": "Aprovar bibliografia",
    "aguardando_pdfs": "Marcar PDFs como baixados",
    "pdfs_adicionados": "Gerar texto da aula",
    "texto_em_producao": "Enviar para revisão",
    "texto_pronto_revisao": "Enviar para revisão",
    "texto_revisado": "Gerar PPTX",
    "slides_em_producao": "Gerar PPTX",
    "pptx_pronto": "Marcar como concluída",
    "revisao_final": "Marcar como concluída",
    "concluida": "Concluída",
    "erro_bloqueada": "Resolver bloqueio",
}


class HistoricoEvento(BaseModel):
    timestamp: datetime
    acao: str
    de_status: StatusKey
    para_status: StatusKey
    mensagem: Optional[str] = None


class ArquivosAula(BaseModel):
    bibliografia_dir: Optional[str] = None
    livros_extraidos_dir: Optional[str] = None
    artigos_dir: Optional[str] = None
    texto_aula: Optional[str] = None
    revisao: Optional[str] = None
    pptx_final: Optional[str] = None


class PdfInfo(BaseModel):
    total: int = 0
    baixados: int = 0
    nomes: list[str] = Field(default_factory=list)


class AulaItem(BaseModel):
    id: str
    modulo_num: int
    modulo_nome: str
    aula_num: int
    aula_tema: str
    status: StatusKey = "proximas_aulas"
    proxima_acao: str = "Gerar bibliografia"
    pasta_relativa: str
    pasta_absoluta: str
    pendencias: list[str] = Field(default_factory=list)
    pdfs: PdfInfo = Field(default_factory=PdfInfo)
    arquivos: ArquivosAula = Field(default_factory=ArquivosAula)
    texto_preview: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    historico: list[HistoricoEvento] = Field(default_factory=list)
    drive_folder_id: Optional[str] = None
    drive_subfolders: dict[str, str] = Field(default_factory=dict)
    ai_artifacts: dict[str, str] = Field(default_factory=dict)


class AulasState(BaseModel):
    version: int = 1
    updated_at: datetime
    aulas: list[AulaItem] = Field(default_factory=list)


class ActionRequest(BaseModel):
    note: Optional[str] = None


class ActionResponse(BaseModel):
    ok: bool
    message: str
    aula: Optional[AulaItem] = None


class DriveUploadRequest(BaseModel):
    local_relative_path: str
    target_subfolder: Optional[str] = None
    target_name: Optional[str] = None


class ColumnsResponse(BaseModel):
    columns: list[tuple[StatusKey, str]]
