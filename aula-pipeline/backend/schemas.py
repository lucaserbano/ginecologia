from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


StatusKey = Literal[
    "proximas_aulas",
    "bibliografia_em_geracao",
    "bibliografia_pronta",
    "pdfs_baixados",
    "texto_feito",
    "texto_editado",
    "pptx_gerado",
    "pptx_finalizado",
    "erro_bloqueada",
]

ACTION_KEY_BY_ROUTE = {
    "gerar-bibliografia": "gerar_bibliografia",
    "marcar-pdfs-baixados": "marcar_pdfs_baixados",
    "salvar-texto-inicial": "salvar_texto_inicial",
    "concluir-edicao": "concluir_edicao",
    "gerar-pptx": "gerar_pptx",
    "marcar-imagens-prontas": "marcar_imagens_prontas",
    "abrir-pasta": "abrir_pasta",
    "avancar-etapa": "avancar_etapa",
    "voltar-etapa": "voltar_etapa",
}

STATUS_COLUMNS: list[tuple[StatusKey, str]] = [
    ("proximas_aulas", "Próximas aulas"),
    ("bibliografia_em_geracao", "Bibliografia em geração"),
    ("bibliografia_pronta", "Bibliografia pronta para download"),
    ("pdfs_baixados", "PDFs baixados"),
    ("texto_feito", "Texto feito"),
    ("texto_editado", "Texto editado"),
    ("pptx_gerado", "PPTX gerado"),
    ("pptx_finalizado", "PPTX pronto"),
    ("erro_bloqueada", "Erro / bloqueada"),
]

STATUS_LABEL_MAP = {k: v for k, v in STATUS_COLUMNS}
STATUS_FLOW: list[StatusKey] = [k for k, _ in STATUS_COLUMNS if k != "erro_bloqueada"]

NEXT_ACTION_BY_STATUS: dict[StatusKey, str] = {
    "proximas_aulas": "Gerar bibliografia",
    "bibliografia_em_geracao": "Aguardando geração",
    "bibliografia_pronta": "Marcar PDFs como baixados",
    "pdfs_baixados": "Salvar texto do NotebookLM",
    "texto_feito": "Concluir edição do texto",
    "texto_editado": "Gerar PPTX",
    "pptx_gerado": "Marcar imagens como prontas",
    "pptx_finalizado": "Concluída",
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
    pptx_final: Optional[str] = None
    pptx_web_view_link: Optional[str] = None


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
    progresso: Optional[str] = None


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


class TextoRequest(BaseModel):
    conteudo: str


class RemoverLinkRequest(BaseModel):
    source: Literal[
        "diretrizes_consensos.md",
        "pubmed_busca.md",
        "uptodate.md",
        "capitulos_livros.md",
        "01_bibliografia.md",
    ]
    url: str


class AdicionarLinkRequest(BaseModel):
    # Só as 4 fontes curadas são editáveis manualmente; 01_bibliografia.md é
    # gerado/consolidado e não recebe inserção manual.
    source: Literal[
        "diretrizes_consensos.md",
        "pubmed_busca.md",
        "uptodate.md",
        "capitulos_livros.md",
    ]
    url: str
    titulo: Optional[str] = None
    meta: Optional[str] = None


class TextoResponse(BaseModel):
    ok: bool
    conteudo: str = ""
    fonte: Literal["drive", "vazio"] = "vazio"


class ColumnsResponse(BaseModel):
    columns: list[tuple[StatusKey, str]]
