from __future__ import annotations

import html
import importlib.util
import json
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from artifact_store import ArtifactWriteResult, format_artifact_result, persist_ai_artifact
from drive_client import build_drive, download_file_to_path, list_children
from drive_sync import upload_local_file_for_aula
from schemas import AulaItem
from settings import (
    BOOKS_DRIVE_FOLDER_ID,
    NCBI_API_KEY,
    NCBI_EMAIL,
    NCBI_TOOL,
    PHASE1_MAX_WEB_RESULTS,
)
from store import REPO_ROOT


GUIDELINE_SOURCES = [
    ("FEBRASGO", "febrasgo.org.br"),
    ("Ministério da Saúde / CONITEC", "www.gov.br"),
    ("WHO", "who.int"),
    ("ACOG", "acog.org"),
    ("RCOG", "rcog.org.uk"),
    ("FIGO", "figo.org"),
]

BOOK_TARGETS = [
    {
        "key": "tratado",
        "drive_name": "tratado-de-ginecologia-da-febrasgo.pdf",
        "index": "livros/tratado-de-ginecologia-da-febrasgo-sumario-paginas.md",
    },
    {
        "key": "williams",
        "drive_name": "Williams Ginecologia.pdf",
        "index": "livros/williams-ginecologia-sumario-paginas.md",
    },
]


@dataclass
class Phase1Result:
    artifacts: list[ArtifactWriteResult] = field(default_factory=list)
    uploaded_books: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_phase1_bibliografia(aula: AulaItem, note: Optional[str]) -> Phase1Result:
    result = Phase1Result()
    generated_at = datetime.utcnow().strftime("%Y-%m-%d")

    pubmed_md = build_pubmed_markdown(aula, generated_at)
    result.artifacts.append(persist_ai_artifact(aula, "pubmed_busca.md", pubmed_md))

    uptodate_md = build_uptodate_markdown(aula, generated_at)
    result.artifacts.append(persist_ai_artifact(aula, "uptodate.md", uptodate_md))

    diretrizes_md = build_guidelines_markdown(aula, generated_at)
    result.artifacts.append(persist_ai_artifact(aula, "diretrizes_consensos.md", diretrizes_md))

    capitulos_md, uploaded_books, book_warnings = build_book_artifacts(aula, generated_at)
    result.uploaded_books.extend(uploaded_books)
    result.warnings.extend(book_warnings)
    result.artifacts.append(persist_ai_artifact(aula, "capitulos_livros.md", capitulos_md))

    consolidated = build_consolidated_markdown(
        aula=aula,
        generated_at=generated_at,
        pubmed_md=pubmed_md,
        uptodate_md=uptodate_md,
        diretrizes_md=diretrizes_md,
        capitulos_md=capitulos_md,
        note=note,
    )
    result.artifacts.append(persist_ai_artifact(aula, "01_bibliografia.md", consolidated))
    return result


def format_phase1_result(result: Phase1Result) -> str:
    artifact_bits = [format_artifact_result(item) for item in result.artifacts]
    uploaded = len(result.uploaded_books)
    warnings = []
    warnings.extend(result.warnings)
    for item in result.artifacts:
        warnings.extend(item.warnings)

    message = f"Artefatos: {len(result.artifacts)} markdown(s); livros extraídos enviados: {uploaded}."
    if artifact_bits:
        message += " " + " | ".join(artifact_bits[:2])
    if warnings:
        message += " Avisos: " + "; ".join(dict.fromkeys(warnings))
    return message


def build_pubmed_markdown(aula: AulaItem, generated_at: str) -> str:
    query = build_pubmed_query(aula)
    rows: list[dict] = []
    error = ""
    try:
        ids = pubmed_esearch(query, retmax=8)
        time.sleep(0.35)
        rows = pubmed_esummary(ids)
    except Exception as exc:
        error = str(exc)

    if rows:
        table_rows = "\n".join(
            "| {priority} | {title} | {year} | {pmid} | https://pubmed.ncbi.nlm.nih.gov/{pmid}/ | {journal} | {reason} |".format(
                priority="Alta" if idx < 3 else "Média",
                title=_escape_table(row.get("title", "")),
                year=_escape_table(row.get("year", "")),
                pmid=_escape_table(row.get("pmid", "")),
                journal=_escape_table(row.get("journal", "")),
                reason=_escape_table("Resultado PubMed priorizado pela consulta específica da aula."),
            )
            for idx, row in enumerate(rows)
        )
    else:
        table_rows = "| - | - | - | - | - | - | - |"

    return f"""# Busca PubMed - M{aula.modulo_num} / Aula {aula.aula_num}

## 1) Metadados da busca
- Data da busca: {generated_at}
- Tema: {aula.aula_tema}
- População: ginecologia
- Recorte clínico: diagnóstico, tratamento, seguimento e tomada de decisão
- Idiomas: português, inglês, espanhol
- Período: últimos 10 anos + revisões relevantes

## 2) Estratégia de busca
### String específica
`{query}`

## 3) Filtros aplicados
- Base: PubMed via NCBI E-utilities
- Quantidade máxima: 8 resultados
- Observação: sem uso de API paga; `NCBI_API_KEY` é opcional e apenas aumenta limite de requisições.

## 4) Artigos selecionados (shortlist)
| Prioridade | Título | Ano | PMID | Link PubMed | Periódico | Motivo da seleção |
|---|---|---:|---|---|---|---|
{table_rows}

## 5) Artigos excluídos relevantes
| Título | Motivo da exclusão |
|---|---|
| - | Triagem humana pendente após leitura de título/resumo. |

## 6) Lacunas de evidência
- Validar manualmente aderência clínica e qualidade metodológica antes de usar no texto final.
{f"- Erro técnico na busca: {error}" if error else ""}
"""


def build_uptodate_markdown(aula: AulaItem, generated_at: str) -> str:
    query = f"site:uptodate.com/contents {aula.aula_tema} gynecology"
    links = public_search(query, allowed=lambda url: _is_uptodate_content(url), limit=3)
    rows = _links_table(links)
    return f"""# UpToDate - M{aula.modulo_num} / Aula {aula.aula_num}

## Metadados
- Data: {generated_at}
- Aula: {aula.id} - {aula.aula_tema}
- População: ginecologia
- Contexto: apoio bibliográfico, sem baixar conteúdo protegido

## Links selecionados (validados)
| Prioridade | Título | Link | Motivo da seleção | Observação |
|---|---|---|---|---|
{rows}

## Queries usadas
- Query principal: `{query}`

## Auditoria
- Total de links candidatos: {len(links)}
- Pendências/lacunas: validar aderência clínica e acesso institucional antes de usar conteúdo.
"""


def build_guidelines_markdown(aula: AulaItem, generated_at: str) -> str:
    found: list[dict] = []
    for source_name, domain in GUIDELINE_SOURCES:
        query = f"site:{domain} {aula.aula_tema} gynecology guideline consensus"
        links = public_search(query, allowed=lambda url, d=domain: d in urllib.parse.urlparse(url).netloc, limit=2)
        for link in links:
            found.append({**link, "source": source_name, "query": query})
        if len(found) >= PHASE1_MAX_WEB_RESULTS:
            break

    if found:
        rows = "\n".join(
            "| {priority} | Guideline/consenso candidato | {title} | {source} | - | internacional/nacional | {url} | Fonte priorizada para triagem humana. |".format(
                priority="Essencial" if idx < 4 else "Complementar",
                title=_escape_table(item["title"]),
                source=_escape_table(item["source"]),
                url=_escape_table(item["url"]),
            )
            for idx, item in enumerate(found[:PHASE1_MAX_WEB_RESULTS])
        )
    else:
        rows = "| - | - | - | - | - | - | - | Nenhum candidato recuperado automaticamente. |"

    return f"""# Diretrizes e Consensos - M{aula.modulo_num} / Aula {aula.aula_num}

## 1) Metadados
- Data da curadoria: {generated_at}
- Tema: {aula.aula_tema}
- População: ginecologia
- Escopo clínico: documentos oficiais e consensos priorizados

## 2) Fontes selecionadas
| Prioridade | Tipo | Título | Entidade | Ano | País/escopo | Link | Motivo da seleção |
|---|---|---|---|---:|---|---|---|
{rows}

## 3) Principais recomendações para a aula
| Fonte | Recomendação-chave | Nível de evidência (se houver) | Impacto prático no manejo |
|---|---|---|---|
| - | Extração de recomendações depende da leitura humana do documento candidato. | - | - |

## 4) Conflitos entre diretrizes
| Tema do conflito | Fonte A | Fonte B | Diferença prática | Como abordar na aula |
|---|---|---|---|---|
| - | - | - | - | Pendente após leitura. |

## 5) Lacunas
- Esta etapa reúne links oficiais candidatos; validação de conteúdo e ano deve ser feita antes da redação final.
"""


def build_book_artifacts(aula: AulaItem, generated_at: str) -> tuple[str, list[dict], list[str]]:
    uploaded: list[dict] = []
    warnings: list[str] = []
    rows: list[str] = []
    try:
        service = build_drive(interactive=False)
        available = {item.get("name"): item for item in list_children(service, BOOKS_DRIVE_FOLDER_ID)}
    except Exception as exc:
        available = {}
        warnings.append(f"não foi possível acessar pasta de livros no Drive: {exc}")

    extractor = _load_book_extractor(warnings)
    with tempfile.TemporaryDirectory(prefix="gineco-books-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        for book in BOOK_TARGETS:
            title = book["key"]
            drive_name = book["drive_name"]
            item = available.get(drive_name)
            index_path = REPO_ROOT / book["index"]
            if not item:
                rows.append(f"| {title} | {aula.aula_tema} | - | - | 0 | - | PDF não encontrado no Drive ({drive_name}). |")
                continue
            if not extractor or not index_path.exists():
                rows.append(f"| {title} | {aula.aula_tema} | - | - | 0 | - | Script ou sumário indisponível no backend. |")
                continue

            try:
                source_pdf = tmp_dir / drive_name
                download_file_to_path(service, item["id"], source_pdf)
                selected, confidence, pages = _extract_book_pages(
                    extractor=extractor,
                    book_title=title,
                    source_pdf=source_pdf,
                    index_path=index_path,
                    output_path=tmp_dir / f"{title}_{_safe_slug(aula.id + '_' + aula.aula_tema)}.pdf",
                    query=aula.aula_tema,
                )
                uploaded_file = upload_local_file_for_aula(
                    aula=aula,
                    drive_service=service,
                    local_path=tmp_dir / f"{title}_{_safe_slug(aula.id + '_' + aula.aula_tema)}.pdf",
                    target_subfolder="02_livros_extraidos",
                )
                uploaded.append(uploaded_file)
                rows.append(
                    f"| {title} | {aula.aula_tema} | {_escape_table(selected.title)} | {selected.start}-{selected.end} | {confidence:.2f} | {uploaded_file.get('name')} | {pages} página(s) extraídas e enviadas ao Drive. |"
                )
            except Exception as exc:
                warnings.append(f"falha ao extrair {title}: {exc}")
                rows.append(f"| {title} | {aula.aula_tema} | - | - | 0 | - | Erro técnico: {_escape_table(str(exc))}. |")

    table = "\n".join(rows) if rows else "| - | - | - | - | - | - | Nenhum livro processado. |"
    return f"""# Capítulos de livros - M{aula.modulo_num} / Aula {aula.aula_num}

## Metadados
- Data da indexação: {generated_at}
- Tema: {aula.aula_tema}
- Pasta Drive dos livros: {BOOKS_DRIVE_FOLDER_ID}

## Extrações
| Livro | Tema solicitado | Capítulo selecionado | Páginas | Confiança | Arquivo gerado | Motivo/observação |
|---|---|---|---|---:|---|---|
{table}

## Pendências
{_warnings_list(warnings)}
""", uploaded, warnings


def build_consolidated_markdown(
    aula: AulaItem,
    generated_at: str,
    pubmed_md: str,
    uptodate_md: str,
    diretrizes_md: str,
    capitulos_md: str,
    note: Optional[str],
) -> str:
    return f"""# Bibliografia Inicial - {aula.id}

## Metadados
- Data: {generated_at}
- Módulo: M{aula.modulo_num} - {aula.modulo_nome}
- Aula: {aula.aula_num} - {aula.aula_tema}
- Observação do usuário: {note or "nenhuma"}

## Arquivos gerados nesta fase
- `pubmed_busca.md`
- `uptodate.md`
- `diretrizes_consensos.md`
- `capitulos_livros.md`

## Resumo operacional
Esta fase reuniu candidatos bibliográficos rastreáveis e extraiu capítulos de livros quando os PDFs estavam disponíveis no Drive. Nenhuma fonte deve ser considerada aprovada sem triagem humana.

---

{diretrizes_md}

---

{pubmed_md}

---

{uptodate_md}

---

{capitulos_md}
"""


def pubmed_esearch(query: str, retmax: int) -> list[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retmode": "json",
        "sort": "relevance",
        "tool": NCBI_TOOL,
    }
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    data = _fetch_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params)
    return data.get("esearchresult", {}).get("idlist", [])


def pubmed_esummary(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "json",
        "tool": NCBI_TOOL,
    }
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    data = _fetch_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", params)
    result = data.get("result", {})
    rows: list[dict] = []
    for pmid in result.get("uids", []):
        item = result.get(pmid, {})
        pubdate = str(item.get("pubdate", ""))
        year = re.search(r"\d{4}", pubdate)
        rows.append(
            {
                "pmid": pmid,
                "title": _clean_text(item.get("title", "")),
                "year": year.group(0) if year else "",
                "journal": _clean_text(item.get("fulljournalname") or item.get("source", "")),
            }
        )
    return rows


def public_search(query: str, allowed, limit: int) -> list[dict]:
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    results: list[dict] = []
    seen: set[str] = set()
    for href, title in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, flags=re.I | re.S):
        resolved = _resolve_search_href(html.unescape(href))
        if not resolved or resolved in seen:
            continue
        if not allowed(resolved):
            continue
        seen.add(resolved)
        results.append({"title": _clean_text(title), "url": resolved})
        if len(results) >= limit:
            break
    return results


def build_pubmed_query(aula: AulaItem) -> str:
    theme = aula.aula_tema
    return f'("{theme}"[Title/Abstract] OR "{theme}"[MeSH Terms]) AND (gynecology OR women OR female)'


def _fetch_json(base_url: str, params: dict[str, str]) -> dict:
    url = base_url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "GinecoKanban/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_search_href(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc:
        qs = urllib.parse.parse_qs(parsed.query)
        candidate = (qs.get("uddg") or [""])[0]
        if candidate:
            return candidate
    if parsed.scheme in {"http", "https"}:
        return href
    return ""


def _is_uptodate_content(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc == "www.uptodate.com" and parsed.path.startswith("/contents/")


def _links_table(links: list[dict]) -> str:
    if not links:
        return "| - | - | - | Nenhum link candidato recuperado automaticamente. | - |"
    return "\n".join(
        f"| {'Alta' if idx == 0 else 'Média'} | {_escape_table(item['title'])} | {_escape_table(item['url'])} | Candidato recuperado por busca pública filtrada. | Validar aderência antes de uso. |"
        for idx, item in enumerate(links)
    )


def _load_book_extractor(warnings: list[str]):
    script_path = REPO_ROOT / "livros" / "extrair_tema_tratado.py"
    if not script_path.exists():
        warnings.append(f"script de extração ausente: {script_path}")
        return None
    spec = importlib.util.spec_from_file_location("gineco_book_extractor", script_path)
    if not spec or not spec.loader:
        warnings.append("falha ao carregar módulo de extração de livros")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract_book_pages(extractor, book_title: str, source_pdf: Path, index_path: Path, output_path: Path, query: str):
    entries = extractor.load_index(index_path)
    ranked = extractor.rank_entries(query, entries)
    if not ranked:
        raise RuntimeError("nenhuma entrada no sumário")
    selected, confidence = ranked[0]
    pages = extractor.extract_pages(source_pdf, [selected], output_path, dedupe=True)
    return selected, confidence, pages


def _safe_slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text or "aula"


def _escape_table(value: str) -> str:
    return _clean_text(value).replace("|", "\\|")


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _warnings_list(warnings: list[str]) -> str:
    if not warnings:
        return "- Nenhuma pendência técnica registrada."
    return "\n".join(f"- {item}" for item in warnings)
