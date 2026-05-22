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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from artifact_store import ArtifactWriteResult, format_artifact_result, persist_ai_artifact
from drive_client import build_drive, download_file_to_path, list_children
from drive_sync import upload_local_file_for_aula
from openrouter_client import generate_text
from schemas import AulaItem
from settings import (
    BOOKS_DRIVE_FOLDER_ID,
    ENABLE_GEMINI_GROUNDING,
    GOOGLE_CSE_API_KEY,
    GOOGLE_CSE_CX,
    NCBI_API_KEY,
    NCBI_EMAIL,
    NCBI_TOOL,
)
from store import REPO_ROOT


GUIDELINE_SOURCES_PT = [
    ("FEBRASGO", "febrasgo.org.br"),
    ("Ministério da Saúde / CONITEC", "www.gov.br"),
]

GUIDELINE_SOURCES_EN = [
    ("ACOG", "acog.org"),
    ("RCOG", "rcog.org.uk"),
    ("FIGO", "figo.org"),
    ("WHO", "who.int"),
    ("NAMS", "menopause.org"),
    ("ESHRE", "eshre.eu"),
]

PUBMED_LIMIT = 5
UPTODATE_LIMIT = 5
GUIDELINES_LIMIT = 8

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
class SearchTerms:
    tema_en: str
    pubmed_query: str
    uptodate_query: str
    guideline_terms_en: str
    guideline_terms_pt: str
    source: str = "gemini"


@dataclass
class Phase1Result:
    artifacts: list[ArtifactWriteResult] = field(default_factory=list)
    uploaded_books: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_phase1_bibliografia(
    aula: AulaItem,
    note: Optional[str],
    on_progress: Optional[Callable[[str], None]] = None,
) -> Phase1Result:
    result = Phase1Result()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _step(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    _step("Gerando termos de busca")
    terms = _generate_search_terms(aula, result.warnings)

    _step("Buscando no PubMed")
    pubmed_md, pubmed_links = build_pubmed_markdown(aula, generated_at, terms)
    result.artifacts.append(persist_ai_artifact(aula, "pubmed_busca.md", pubmed_md))

    _step("Buscando no UpToDate")
    uptodate_md, uptodate_links = build_uptodate_markdown(aula, generated_at, terms)
    result.artifacts.append(persist_ai_artifact(aula, "uptodate.md", uptodate_md))

    _step("Buscando diretrizes e consensos")
    diretrizes_md, guideline_links = build_guidelines_markdown(aula, generated_at, terms)
    result.artifacts.append(persist_ai_artifact(aula, "diretrizes_consensos.md", diretrizes_md))

    _step("Extraindo capítulos de livros")
    capitulos_md, uploaded_books, book_warnings = build_book_artifacts(aula, generated_at)
    result.uploaded_books.extend(uploaded_books)
    result.warnings.extend(book_warnings)
    result.artifacts.append(persist_ai_artifact(aula, "capitulos_livros.md", capitulos_md))

    _step("Consolidando bibliografia")
    consolidated = build_consolidated_markdown(
        aula=aula,
        generated_at=generated_at,
        terms=terms,
        pubmed_links=pubmed_links,
        uptodate_links=uptodate_links,
        guideline_links=guideline_links,
        capitulos_md=capitulos_md,
        note=note,
    )
    result.artifacts.append(persist_ai_artifact(aula, "01_bibliografia.md", consolidated))
    return result


def format_phase1_result(result: Phase1Result) -> str:
    artifact_bits = [format_artifact_result(item) for item in result.artifacts]
    uploaded = len(result.uploaded_books)
    warnings: list[str] = []
    warnings.extend(result.warnings)
    for item in result.artifacts:
        warnings.extend(item.warnings)

    message = f"Artefatos: {len(result.artifacts)} markdown(s); livros extraídos enviados: {uploaded}."
    if artifact_bits:
        message += " " + " | ".join(artifact_bits[:2])
    if warnings:
        message += " Avisos: " + "; ".join(dict.fromkeys(warnings))
    return message


# ---------------------------------------------------------------------------
# Geração de queries (Gemini)
# ---------------------------------------------------------------------------


INTERNATIONAL_GUIDELINES_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "guidelines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "source": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "url": {"type": "STRING"},
                },
                "required": ["source", "title", "url"],
            },
        }
    },
    "required": ["guidelines"],
}


SEARCH_TERMS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tema_en": {"type": "STRING"},
        "pubmed_query": {"type": "STRING"},
        "uptodate_query": {"type": "STRING"},
        "guideline_terms_en": {"type": "STRING"},
        "guideline_terms_pt": {"type": "STRING"},
    },
    "required": [
        "tema_en",
        "pubmed_query",
        "uptodate_query",
        "guideline_terms_en",
        "guideline_terms_pt",
    ],
}


def _generate_search_terms(aula: AulaItem, warnings: list[str]) -> SearchTerms:
    fallback = _fallback_terms(aula)
    sys_prompt = (
        "Você é um curador de bibliografia médica. Para uma aula de ginecologia em português, "
        "gere termos de busca rastreáveis em inglês para PubMed/UpToDate/diretrizes internacionais "
        "e em português para FEBRASGO/Ministério da Saúde. Nunca invente termos sem relação clínica."
    )
    user_prompt = f"""Aula: M{aula.modulo_num} - {aula.modulo_nome} / Aula {aula.aula_num} - {aula.aula_tema}

Gere termos de busca estruturados:
- tema_en: tradução curta e clínica do tema (1 linha em inglês).
- pubmed_query: string PubMed em inglês usando MeSH e operadores booleanos. NÃO incluir filtros de tipo de estudo ou data (eu adiciono depois).
- uptodate_query: termos em inglês cobrindo a tríade clínica (apresentação clínica + diagnóstico + tratamento) para retornar páginas /contents/ relevantes para uma aula médica. Evite focar em "patient education" ou "beyond the basics" (são páginas para leigos). Sem operadores booleanos.
- guideline_terms_en: termos em inglês para buscar diretrizes em sites internacionais (ACOG, RCOG, FIGO, WHO, NAMS, ESHRE).
- guideline_terms_pt: termos em português para buscar diretrizes nacionais (FEBRASGO, Ministério da Saúde).

Exemplo para 'Sindrome dos ovarios policisticos':
- tema_en: Polycystic ovary syndrome
- pubmed_query: ("polycystic ovary syndrome"[MeSH Terms] OR "PCOS"[Title/Abstract]) AND (diagnosis OR treatment OR management)
- uptodate_query: polycystic ovary syndrome clinical manifestations diagnosis treatment management
- guideline_terms_en: polycystic ovary syndrome PCOS guideline
- guideline_terms_pt: sindrome dos ovarios policisticos SOP"""
    try:
        raw = generate_text(
            sys_prompt,
            user_prompt,
            temperature=0.1,
            max_tokens=4000,
            response_schema=SEARCH_TERMS_SCHEMA,
            thinking_budget=0,
        )
    except Exception as exc:
        warnings.append(f"Gemini indisponível para queries da fase 1: {exc}")
        return fallback

    try:
        data = json.loads(raw)
    except Exception:
        data = _extract_json(raw)
    if not data:
        warnings.append("Gemini retornou JSON inválido para queries da fase 1.")
        return fallback

    return SearchTerms(
        tema_en=_clean_text(data.get("tema_en")) or fallback.tema_en,
        pubmed_query=_clean_text(data.get("pubmed_query")) or fallback.pubmed_query,
        uptodate_query=_clean_text(data.get("uptodate_query")) or fallback.uptodate_query,
        guideline_terms_en=_clean_text(data.get("guideline_terms_en")) or fallback.guideline_terms_en,
        guideline_terms_pt=_clean_text(data.get("guideline_terms_pt")) or fallback.guideline_terms_pt,
        source="gemini",
    )


def _fallback_terms(aula: AulaItem) -> SearchTerms:
    tema = aula.aula_tema or ""
    return SearchTerms(
        tema_en=tema,
        pubmed_query=f'("{tema}"[Title/Abstract]) AND (gynecology OR women OR female)',
        uptodate_query=f"{tema} gynecology",
        guideline_terms_en=f"{tema} gynecology guideline",
        guideline_terms_pt=f"{tema} ginecologia",
        source="fallback",
    )


def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------


def build_pubmed_markdown(aula: AulaItem, generated_at: str, terms: SearchTerms) -> tuple[str, list[dict]]:
    base_query = terms.pubmed_query
    filters = (
        ' AND humans[Filter] AND ("2019"[PDAT]:"3000"[PDAT]) AND '
        '(review[pt] OR meta-analysis[pt] OR randomized controlled trial[pt] OR practice guideline[pt])'
    )
    rows: list[dict] = []
    used_query = base_query + filters
    error = ""

    try:
        ids = pubmed_esearch(used_query, retmax=PUBMED_LIMIT * 2)
        if len(ids) < 3:
            # Fallback sem filtro de tipo
            used_query = base_query + ' AND humans[Filter] AND ("2019"[PDAT]:"3000"[PDAT])'
            ids = pubmed_esearch(used_query, retmax=PUBMED_LIMIT * 2)
        time.sleep(0.35)
        rows = pubmed_esummary(ids[: PUBMED_LIMIT * 2])
    except Exception as exc:
        error = str(exc)

    rows = rows[:PUBMED_LIMIT]
    links = [
        {
            "title": row.get("title", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{row.get('pmid')}/",
            "year": row.get("year", ""),
            "journal": row.get("journal", ""),
            "ptype": row.get("ptype", ""),
            "pmid": row.get("pmid", ""),
        }
        for row in rows
        if row.get("pmid")
    ]

    if links:
        items = "\n".join(_format_pubmed_line(link) for link in links)
    else:
        items = "- (nenhum resultado retornado pela busca automática — refinar query manualmente)"

    error_line = f"\n\n> Erro técnico na busca: {error}" if error else ""

    md = f"""# PubMed — M{aula.modulo_num} / Aula {aula.aula_num} · {aula.aula_tema}

**Tema (EN):** {terms.tema_en}
**Data:** {generated_at}
**Query:** `{used_query}`
**Fonte das queries:** {terms.source}

## Artigos selecionados ({len(links)})
{items}

## Lacunas
- Validar aderência clínica e qualidade metodológica antes de citar no texto.
- Se quiser ampliar a busca, remover o filtro de tipo de estudo ou expandir a janela temporal.{error_line}
"""
    return md, links


def _format_pubmed_line(link: dict) -> str:
    meta_bits = []
    if link.get("year"):
        meta_bits.append(link["year"])
    if link.get("journal"):
        meta_bits.append(link["journal"])
    if link.get("ptype"):
        meta_bits.append(link["ptype"])
    meta = " · ".join(meta_bits)
    title = link.get("title") or "(sem título)"
    suffix = f" — {meta}" if meta else ""
    return f"- [{title}]({link['url']}){suffix}"


# ---------------------------------------------------------------------------
# UpToDate
# ---------------------------------------------------------------------------


def build_uptodate_markdown(aula: AulaItem, generated_at: str, terms: SearchTerms) -> tuple[str, list[dict]]:
    # Busca mais candidatos do que o limite final: o filtro /contents/, a
    # deduplicacao e o ranker descartam parte, entao precisamos de folga.
    candidates = domain_search(domain="www.uptodate.com", terms=terms.uptodate_query, limit=15)
    valid = [c for c in candidates if _is_uptodate_content(c["url"])]
    ranked = _rank_uptodate_links(valid)
    links = _dedupe_uptodate(ranked)[:UPTODATE_LIMIT]

    if links:
        items = "\n".join(f"- [{link['title']}]({link['url']})" for link in links)
    else:
        items = "- (nenhum link `/contents/` encontrado — refinar termos manualmente)"

    md = f"""# UpToDate — M{aula.modulo_num} / Aula {aula.aula_num} · {aula.aula_tema}

**Data:** {generated_at}
**Termos:** `{terms.uptodate_query}`

## Links selecionados ({len(links)})
{items}

## Observações
- Apenas links com prefixo `https://www.uptodate.com/contents/` são aceitos.
- Acesso institucional necessário para conteúdo completo.
"""
    return md, links


# ---------------------------------------------------------------------------
# Diretrizes / Consensos
# ---------------------------------------------------------------------------


def build_guidelines_markdown(aula: AulaItem, generated_at: str, terms: SearchTerms) -> tuple[str, list[dict]]:
    found: list[dict] = []
    search_engine = "google_cse" if (GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX) else "duckduckgo"

    # Nacionais: busca por dominio (CSE com fallback DDG)
    for source_name, domain in GUIDELINE_SOURCES_PT:
        if len(found) >= GUIDELINES_LIMIT:
            break
        links = domain_search(domain=domain, terms=terms.guideline_terms_pt, limit=3)
        for link in _rank_guideline_links(links):
            if len(found) >= GUIDELINES_LIMIT:
                break
            if any(existing["url"] == link["url"] for existing in found):
                continue
            found.append({**link, "source": source_name, "lang": "pt"})

    # Internacionais: Gemini sugere URL canonica + validacao HTTP
    if len(found) < GUIDELINES_LIMIT:
        suggested = _suggest_international_guidelines(aula, terms.guideline_terms_en)
        for link in suggested:
            if len(found) >= GUIDELINES_LIMIT:
                break
            if any(existing["url"] == link["url"] for existing in found):
                continue
            link.setdefault("lang", "en")
            link["is_pdf"] = link["url"].lower().endswith(".pdf") or ".pdf?" in link["url"].lower()
            found.append(link)

    if found:
        items = "\n".join(
            f"- **{link['source']}** — [{link['title']}]({link['url']})"
            + (" · PDF" if link.get("is_pdf") else "")
            for link in found[:GUIDELINES_LIMIT]
        )
    else:
        items = "- (nenhum candidato encontrado nas fontes oficiais — refinar termos manualmente)"

    md = f"""# Diretrizes e Consensos — M{aula.modulo_num} / Aula {aula.aula_num} · {aula.aula_tema}

**Data:** {generated_at}
**Termos (PT):** `{terms.guideline_terms_pt}`
**Termos (EN):** `{terms.guideline_terms_en}`
**Buscador:** {search_engine}

## Fontes selecionadas ({len(found)})
{items}

## Fontes consultadas
- Nacionais: {", ".join(name for name, _ in GUIDELINE_SOURCES_PT)}
- Internacionais: {", ".join(name for name, _ in GUIDELINE_SOURCES_EN)}

## Observações
- PDFs oficiais aparecem com marca `· PDF`.
- Sem extrair recomendações automaticamente — leitura humana obrigatória antes do texto.
"""
    return md, found[:GUIDELINES_LIMIT]


UPTODATE_CLINICAL_BOOSTS = (
    "clinical-manifestations",
    "diagnosis",
    "treatment",
    "management",
    "pathogenesis",
    "pathophysiology",
    "etiology",
    "epidemiology",
    "screening",
    "prevention",
    "evaluation",
    "approach",
    "overview",
)

UPTODATE_LAY_PENALTIES = (
    "patient-education",
    "beyond-the-basics",
    "the-basics",
    "patient-information",
)


def _suggest_international_guidelines(aula: AulaItem, terms_en: str) -> list[dict]:
    """Lista diretrizes oficiais (ACOG/RCOG/FIGO/WHO/NAMS/ESHRE) com URLs
    canonicas, valida cada URL via HTTP.

    Com `ENABLE_GEMINI_GROUNDING`, o modelo BUSCA na web (Grounding com
    Google Search) em vez de responder pela memoria - reduz alucinacao de
    URL. Grounding e incompativel com structured output, entao pedimos o
    JSON no proprio prompt e extraimos com `_extract_json`.
    """
    sources = ", ".join(name for name, _ in GUIDELINE_SOURCES_EN)
    grounded = ENABLE_GEMINI_GROUNDING

    if grounded:
        sys_prompt = (
            "Voce e um curador de diretrizes medicas. Use a busca do Google "
            "para encontrar diretrizes oficiais REAIS e atuais publicadas pelas "
            "principais sociedades internacionais de ginecologia. Confirme cada "
            "URL na busca antes de incluir - nao invente URLs. Responda APENAS "
            "com um objeto JSON, sem texto antes ou depois."
        )
    else:
        sys_prompt = (
            "Voce e um curador de diretrizes medicas. Liste diretrizes oficiais "
            "publicadas pelas principais sociedades internacionais de ginecologia "
            "para o tema da aula, com URLs canonicas do dominio oficial. As URLs "
            "serao validadas via HTTP, entao um link errado e descartado "
            "automaticamente - prefira listar mais candidatos plausiveis a omitir."
        )

    json_hint = (
        '\n\nFormato da resposta (JSON estrito):\n'
        '{"guidelines": [{"source": "ACOG", "title": "...", "url": "https://..."}]}'
    ) if grounded else ""

    user_prompt = f"""Tema da aula: {aula.aula_tema}
Termos de busca (EN): {terms_en}

Para cada uma das sociedades ({sources}), liste 2 a 3 documentos oficiais sobre o tema (total esperado: 10 a 18 candidatos).

Priorize:
- Practice Bulletin, Committee Opinion (ACOG)
- Green-top Guideline, Scientific Impact Paper (RCOG)
- Guideline, Position Statement (FIGO, ESHRE, NAMS)
- Recommendations, Guideline (WHO)
- PDF direto da diretriz quando souber

Evite (mas pode incluir como fallback se nao souber a versao tecnica):
- FAQs e paginas de educacao de paciente
- News, blog posts, paginas de membership

Para cada documento informe:
- source: nome da sociedade (ACOG, RCOG, FIGO, WHO, NAMS ou ESHRE)
- title: titulo do documento (com numero/codigo quando aplicavel, ex.: "Practice Bulletin #194")
- url: URL canonica no dominio oficial (acog.org, rcog.org.uk, figo.org, who.int, menopause.org, eshre.eu).

Lembre: URLs sao validadas via HTTP - listar mais candidatos plausiveis e melhor do que omitir.{json_hint}"""
    try:
        raw = generate_text(
            sys_prompt,
            user_prompt,
            temperature=0.0,
            max_tokens=8000,
            response_schema=None if grounded else INTERNATIONAL_GUIDELINES_SCHEMA,
            thinking_budget=0,
            grounding=grounded,
        )
    except Exception as exc:
        print(f"[fase1/diretrizes] Gemini falhou: {exc}", flush=True)
        return []

    try:
        data = json.loads(raw)
    except Exception:
        data = _extract_json(raw) or {}

    raw_items = data.get("guidelines") or []
    allowed_domains = [d for _, d in GUIDELINE_SOURCES_EN]
    validated: list[dict] = []
    seen_urls: set[str] = set()
    for item in raw_items:
        url = (item.get("url") or "").strip()
        source = (item.get("source") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not source or not title:
            continue
        if url in seen_urls:
            continue
        parsed = urllib.parse.urlparse(url)
        if not any(parsed.netloc.endswith(d) for d in allowed_domains):
            continue
        if not _validate_url(url):
            continue
        seen_urls.add(url)
        validated.append({"source": source, "title": title, "url": url})
    print(f"[fase1/diretrizes] Gemini={len(raw_items)} | validadas={len(validated)}", flush=True)
    return validated


SOFT_404_MARKERS = (
    "notfound",
    "not-found",
    "404",
    "page-not-found",
    "/error",
    "pagenotfound",
)


def _validate_url(url: str, timeout: int = 8) -> bool:
    """Confirma que a URL responde 200/3xx e nao caiu em soft-404.

    Sites como ESHRE retornam 302 para /NotFound.aspx em URLs inexistentes;
    aceitar 200 cego deixaria alucinacao passar. Por isso checamos a URL
    final (apos redirects) por marcadores tipicos de pagina de erro.
    """
    # GET primeiro porque alguns sites (ACOG) cacheiam HEAD com 200 mesmo
    # em URLs inexistentes; HEAD entra so como fallback de rede.
    for method in ("GET", "HEAD"):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 GinecoKanban/1.0",
                    "Accept": "*/*",
                    **({"Range": "bytes=0-2047"} if method == "GET" else {}),
                },
                method=method,
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if not (200 <= resp.status < 400):
                    continue
                final_url = (resp.geturl() or url).lower()
                if any(marker in final_url for marker in SOFT_404_MARKERS):
                    return False
                return True
        except Exception:
            continue
    return False


def _dedupe_uptodate(links: list[dict]) -> list[dict]:
    """Remove paginas UpToDate equivalentes: variantes `/print` e topicos
    com ID numerico que apontam para o mesmo conteudo de uma versao com
    slug. Detecta por URL normalizada (sem `/print`) e por titulo
    normalizado (UpToDate da o mesmo titulo para o slug e o ID numerico)."""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []
    for link in links:
        url = (link.get("url") or "").lower().rstrip("/")
        if url.endswith("/print"):
            url = url[: -len("/print")]
        title = re.sub(r"\s*[-–—]\s*uptodate\s*$", "", (link.get("title") or "").lower().strip())
        title = re.sub(r"\s+", " ", title)
        if url in seen_urls or (title and title in seen_titles):
            continue
        seen_urls.add(url)
        if title:
            seen_titles.add(title)
        out.append(link)
    return out


def _rank_uptodate_links(links: list[dict]) -> list[dict]:
    """Prioriza paginas clinicas (apresentacao, diagnostico, tratamento) e
    penaliza paginas de educacao de paciente / linguagem leiga."""
    scored: list[tuple[int, int, dict]] = []
    for idx, link in enumerate(links):
        slug = urllib.parse.urlparse(link["url"]).path.lower()
        score = 0
        for term in UPTODATE_CLINICAL_BOOSTS:
            if term in slug:
                score += 3
        for term in UPTODATE_LAY_PENALTIES:
            if term in slug:
                score -= 5
        # Empate: preserva ordem original do buscador.
        scored.append((-score, idx, link))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [item[2] for item in scored]


def _rank_guideline_links(links: list[dict]) -> list[dict]:
    annotated = []
    for link in links:
        url = link["url"]
        is_pdf = url.lower().endswith(".pdf") or ".pdf?" in url.lower()
        annotated.append({**link, "is_pdf": is_pdf})
    annotated.sort(key=lambda x: (0 if x["is_pdf"] else 1, len(x["url"])))
    return annotated


# ---------------------------------------------------------------------------
# Livros (mantido — está funcionando bem)
# ---------------------------------------------------------------------------


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
                rows.append(f"- **{title}**: PDF não encontrado no Drive (`{drive_name}`).")
                continue
            if not extractor or not index_path.exists():
                rows.append(f"- **{title}**: script ou sumário indisponível no backend.")
                continue

            try:
                source_pdf = tmp_dir / drive_name
                download_file_to_path(service, item["id"], source_pdf)
                output_path = tmp_dir / f"{title}_{_safe_slug(aula.id + '_' + aula.aula_tema)}.pdf"
                selected, confidence, pages = _extract_book_pages(
                    extractor=extractor,
                    book_title=title,
                    source_pdf=source_pdf,
                    index_path=index_path,
                    output_path=output_path,
                    query=aula.aula_tema,
                )
                uploaded_file = upload_local_file_for_aula(
                    aula=aula,
                    drive_service=service,
                    local_path=output_path,
                    target_subfolder="02_livros_extraidos",
                )
                uploaded.append(uploaded_file)
                view_link = uploaded_file.get("webViewLink")
                file_name = uploaded_file.get("name") or output_path.name
                file_link = f"[{file_name}]({view_link})" if view_link else file_name
                rows.append(
                    f"- **{title}** — capítulo: {selected.title} (p. {selected.start}-{selected.end}) · "
                    f"confiança {confidence:.2f} · {pages} pág. extraídas → {file_link}"
                )
            except Exception as exc:
                warnings.append(f"falha ao extrair {title}: {exc}")
                rows.append(f"- **{title}**: erro técnico — {exc}")

    table = "\n".join(rows) if rows else "- (nenhum livro processado)"
    md = f"""# Capítulos de livros — M{aula.modulo_num} / Aula {aula.aula_num} · {aula.aula_tema}

**Data:** {generated_at}
**Pasta Drive dos livros:** `{BOOKS_DRIVE_FOLDER_ID}`

## Extrações
{table}

## Pendências
{_warnings_list(warnings)}
"""
    return md, uploaded, warnings


# ---------------------------------------------------------------------------
# Consolidação
# ---------------------------------------------------------------------------


def build_consolidated_markdown(
    aula: AulaItem,
    generated_at: str,
    terms: SearchTerms,
    pubmed_links: list[dict],
    uptodate_links: list[dict],
    guideline_links: list[dict],
    capitulos_md: str,
    note: Optional[str],
) -> str:
    pubmed_block = "\n".join(_format_pubmed_line(link) for link in pubmed_links) or "- (sem resultados)"
    uptodate_block = "\n".join(f"- [{link['title']}]({link['url']})" for link in uptodate_links) or "- (sem resultados)"
    guideline_block = "\n".join(
        f"- **{link['source']}** — [{link['title']}]({link['url']})"
        + (" · PDF" if link.get("is_pdf") else "")
        for link in guideline_links
    ) or "- (sem resultados)"

    return f"""# Bibliografia — {aula.id} · {aula.aula_tema}

**Módulo:** M{aula.modulo_num} - {aula.modulo_nome}
**Aula:** {aula.aula_num} - {aula.aula_tema}
**Data:** {generated_at}
**Tema (EN):** {terms.tema_en}
**Observação do usuário:** {note or "nenhuma"}

## Diretrizes e Consensos
{guideline_block}

## PubMed
{pubmed_block}

## UpToDate
{uptodate_block}

---

{capitulos_md}
"""


# ---------------------------------------------------------------------------
# Helpers de busca
# ---------------------------------------------------------------------------


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
        pubtypes = item.get("pubtype") or []
        rows.append(
            {
                "pmid": pmid,
                "title": _clean_text(item.get("title", "")),
                "year": year.group(0) if year else "",
                "journal": _clean_text(item.get("fulljournalname") or item.get("source", "")),
                "ptype": _summarize_pubtype(pubtypes),
            }
        )
    return rows


def _summarize_pubtype(pubtypes: list[str]) -> str:
    priority = [
        "Practice Guideline",
        "Guideline",
        "Meta-Analysis",
        "Systematic Review",
        "Review",
        "Randomized Controlled Trial",
        "Clinical Trial",
    ]
    found = [p for p in pubtypes if isinstance(p, str)]
    for label in priority:
        if any(label.lower() == p.lower() for p in found):
            return label
    return found[0] if found else ""


def domain_search(domain: str, terms: str, limit: int) -> list[dict]:
    """Busca restrita a um dominio. Usa Google CSE se configurado; senao
    (ou se o CSE retornar vazio) cai para DuckDuckGo Lite com `site:`.

    O CSE custom engine so indexa os sites cadastrados nele (FEBRASGO/MS).
    Para dominios fora dessa lista (ex.: uptodate.com) o CSE retorna 0
    resultados sem erro - por isso o fallback dispara tambem em lista vazia,
    nao so em excecao.
    """
    if GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX:
        try:
            results = google_cse_search(query=terms, site=domain, limit=limit)
            if results:
                return results
        except Exception:
            pass  # fallback silencioso para DDG
    return public_search(
        f"site:{domain} {terms}",
        allowed=lambda url, d=domain: d in urllib.parse.urlparse(url).netloc,
        limit=limit,
    )


def google_cse_search(query: str, site: str, limit: int) -> list[dict]:
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_CX,
        "q": query,
        "siteSearch": site,
        "siteSearchFilter": "i",
        "num": str(min(max(limit, 1), 10)),
    }
    data = _fetch_json("https://www.googleapis.com/customsearch/v1", params)
    items = data.get("items") or []
    results: list[dict] = []
    for item in items:
        link = item.get("link") or ""
        title = _clean_text(item.get("title") or "")
        if not link or not title:
            continue
        if site not in urllib.parse.urlparse(link).netloc:
            continue
        results.append({"title": title, "url": link})
        if len(results) >= limit:
            break
    return results


DDG_USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
)


def public_search(query: str, allowed: Callable[[str], bool], limit: int) -> list[dict]:
    """Busca no DuckDuckGo Lite. O DDG limita IPs de datacenter (Cloud Run)
    de forma intermitente — entao tentamos algumas vezes com backoff e
    User-Agents diferentes antes de desistir."""
    for attempt in range(3):
        results = _ddg_lite_search(query, allowed, limit, ua_index=attempt)
        if results:
            return results
        if attempt < 2:
            time.sleep(2 + attempt * 3)  # 2s, 5s
    return []


def _ddg_lite_search(query: str, allowed: Callable[[str], bool], limit: int, ua_index: int = 0) -> list[dict]:
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    ua = DDG_USER_AGENTS[ua_index % len(DDG_USER_AGENTS)]
    req = urllib.request.Request(url, headers={"User-Agent": ua})
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


def _clean_text(value) -> str:
    if value is None:
        return ""
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _warnings_list(warnings: list[str]) -> str:
    if not warnings:
        return "- Nenhuma pendência técnica registrada."
    return "\n".join(f"- {item}" for item in warnings)
