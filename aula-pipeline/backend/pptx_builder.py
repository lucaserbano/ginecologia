"""Monta o .pptx de uma aula a partir do template `aulas/templates/MX AY.pptx`.

Regras (definidas pelo coordenador):
- Slide 1 (capa): troca apenas número/nome do módulo e número/nome da aula.
- Slide 2 (modelo): caixa de texto à direita. Cada bloco do texto (separado
  por linhas de hífens) vira uma cópia desse slide com o bloco colado.
- Não altera design/layout do template; não altera o texto da aula.
"""

from __future__ import annotations

import copy
import io
import re
from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn

# Linha separadora de blocos: linha contendo apenas 4+ hifens.
_BLOCK_SEP = re.compile(r"(?m)^[ \t]*-{4,}[ \t]*$")

TEMPLATE_FILENAME = "MX AY.pptx"

# Tamanho da fonte (pt) do texto dos slides de conteúdo. O template traz
# 18pt, grande demais para blocos de 600-800 caracteres — 14pt evita que a
# caixa de texto ultrapasse os limites do slide.
CONTENT_FONT_SIZE_PT = 14

# Tokens do template da capa -> valor a inserir (preenchido em runtime).
_CAPA_TOKENS = {
    "modulo_num": "Módulo X",
    "modulo_nome": "[INSERIR NOME DO MÓDULO AQUI]",
    "aula_num": "[insira número da aula aqui]",
    "aula_nome": "[insira nome da aula aqui]",
}


def template_path() -> Path:
    """Resolve o caminho do template, tanto no repo quanto no container."""
    from prompt_loader import TEMPLATE_DIR_CANDIDATES

    for base in TEMPLATE_DIR_CANDIDATES:
        candidate = base / TEMPLATE_FILENAME
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Template '{TEMPLATE_FILENAME}' não encontrado em aulas/templates."
    )


def split_blocks(texto: str) -> list[str]:
    """Divide o texto da aula nos blocos delimitados por linhas de hífens."""
    parts = _BLOCK_SEP.split(texto or "")
    return [b.strip() for b in parts if b.strip()]


def _capa_title_shape(slide):
    """Localiza o placeholder de título da capa (idx=0)."""
    for shape in slide.shapes:
        try:
            if shape.is_placeholder and shape.placeholder_format.idx == 0:
                return shape
        except Exception:
            pass
    for shape in slide.shapes:
        if shape.has_text_frame and _CAPA_TOKENS["modulo_nome"] in shape.text_frame.text:
            return shape
    return slide.shapes[0]


def _fill_capa(slide, modulo_num, modulo_nome: str, aula_num, aula_nome: str) -> None:
    """Troca os tokens da capa preservando a formatação de cada run."""
    repl = {
        _CAPA_TOKENS["modulo_num"]: f"Módulo {modulo_num}",
        _CAPA_TOKENS["modulo_nome"]: str(modulo_nome).strip(),
        _CAPA_TOKENS["aula_num"]: str(aula_num).strip(),
        _CAPA_TOKENS["aula_nome"]: str(aula_nome).strip(),
    }
    title = _capa_title_shape(slide)
    for para in title.text_frame.paragraphs:
        for run in para.runs:
            novo = run.text
            for old, new in repl.items():
                if old in novo:
                    novo = novo.replace(old, new)
            if novo != run.text:
                run.text = novo


def _content_textbox(slide):
    """Localiza a caixa de texto do slide de conteúdo."""
    for shape in slide.shapes:
        if shape.has_text_frame:
            return shape
    raise ValueError("Slide de conteúdo do template não tem caixa de texto.")


def _fill_textbox(slide, block: str, font_size_pt: int = CONTENT_FONT_SIZE_PT) -> None:
    """Cola um bloco de texto na caixa, preservando o parágrafo/run modelo
    do template e ajustando o tamanho da fonte. Linhas em branco viram
    parágrafos vazios com a mesma altura de fonte (espaçamento)."""
    shape = _content_textbox(slide)
    txBody = shape.text_frame._txBody
    paragraphs = txBody.findall(qn("a:p"))
    proto = copy.deepcopy(paragraphs[0])
    for para in paragraphs:
        txBody.remove(para)

    # OOXML expressa o tamanho da fonte em centésimos de ponto.
    sz = str(int(round(font_size_pt * 100)))
    for linha in block.split("\n"):
        novo = copy.deepcopy(proto)
        runs = novo.findall(qn("a:r"))
        # endParaRPr define a altura de parágrafos vazios; alinha à fonte
        # (sem isso, linhas em branco herdariam os 28pt do template).
        end_pr = novo.find(qn("a:endParaRPr"))
        if end_pr is not None:
            end_pr.set("sz", sz)
        if linha.strip() == "":
            # Parágrafo vazio: remove os runs, mantém pPr/endParaRPr.
            for run in runs:
                novo.remove(run)
        else:
            # Mantém só o primeiro run (formatação modelo) e ajusta texto/tamanho.
            for run in runs[1:]:
                novo.remove(run)
            runs[0].find(qn("a:t")).text = linha
            r_pr = runs[0].find(qn("a:rPr"))
            if r_pr is not None:
                r_pr.set("sz", sz)
        txBody.append(novo)


def _duplicate_slide(prs, src_slide):
    """Cria uma nova cópia de `src_slide` no fim da apresentação."""
    dst = prs.slides.add_slide(src_slide.slide_layout)
    # Remove os placeholders herdados do layout.
    for shape in list(dst.shapes):
        shape._element.getparent().remove(shape._element)
    # Copia os shapes do slide de origem.
    for shape in src_slide.shapes:
        dst.shapes._spTree.append(copy.deepcopy(shape._element))
    return dst


def build_pptx(
    texto: str,
    modulo_num,
    modulo_nome: str,
    aula_num,
    aula_nome: str,
    template_file: Path | None = None,
    content_font_size_pt: int = CONTENT_FONT_SIZE_PT,
) -> tuple[bytes, int]:
    """Monta o .pptx da aula. Retorna (bytes, numero_de_slides)."""
    blocks = split_blocks(texto)
    if not blocks:
        blocks = [(texto or "").strip() or "(sem texto)"]

    prs = Presentation(str(template_file or template_path()))
    if len(prs.slides) < 2:
        raise ValueError("Template inválido: esperados 2 slides (capa + conteúdo).")

    _fill_capa(prs.slides[0], modulo_num, modulo_nome, aula_num, aula_nome)

    content_template = prs.slides[1]
    # Cria as cópias necessárias ANTES de preencher (mantém o modelo intacto).
    for _ in range(len(blocks) - 1):
        _duplicate_slide(prs, content_template)

    # Slides de conteúdo ocupam os índices 1..N.
    for idx, block in enumerate(blocks):
        _fill_textbox(prs.slides[idx + 1], block, content_font_size_pt)

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue(), len(prs.slides)
