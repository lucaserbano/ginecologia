"""Funções de download de PDFs usadas pelo runner local.

Separadas do runner.py para serem testáveis isoladamente. As funções puras
(extract_pmid, looks_like_pdf, filename_from_url, slugify) não fazem rede;
as demais usam uma `requests.Session` recebida por parâmetro.
"""
from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

# UA de navegador real reduz bloqueios bobos em alguns servidores de diretrizes.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# --- Puras -----------------------------------------------------------------

def slugify(text: str, fallback: str = "artigo") -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-+", "-", text)
    return (text or fallback)[:120]


def extract_pmid(url: str) -> Optional[str]:
    """Extrai o PMID de uma URL pubmed.ncbi.nlm.nih.gov/<pmid>/."""
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
    return m.group(1) if m else None


def pmcid_from_url(url: str) -> Optional[str]:
    """Extrai o PMCID (PMC123…) de uma URL do PMC."""
    m = re.search(r"(PMC\d+)", url, re.I)
    return m.group(1).upper() if m else None


def looks_like_pdf(content: bytes) -> bool:
    """PDF de verdade começa com %PDF (eventualmente após poucos bytes de BOM)."""
    if not content:
        return False
    head = content[:1024]
    return b"%PDF" in head


def filename_from_url(url: str, fallback: str = "artigo") -> str:
    """Nome de arquivo .pdf razoável a partir da URL."""
    path = urlsplit(url).path
    last = path.rstrip("/").split("/")[-1] if path else ""
    last = last.split("?")[0]
    if last.lower().endswith(".pdf"):
        stem = last[:-4]
    else:
        stem = last
    base = slugify(stem, fallback)
    return f"{base}.pdf"


# --- Com rede --------------------------------------------------------------

def download_pdf(
    url: str,
    dest_dir: Path,
    session,
    referer: Optional[str] = None,
    min_bytes: int = 20_000,
    timeout: int = 60,
) -> Optional[Path]:
    """Baixa `url` se for um PDF de verdade. Retorna o caminho salvo ou None."""
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/pdf,*/*"}
    if referer:
        headers["Referer"] = referer
    try:
        resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
    except Exception:
        return None
    if resp.status_code != 200:
        resp.close()
        return None
    content = resp.content  # stream=True + .content lê tudo uma vez
    ctype = resp.headers.get("Content-Type", "").lower()
    if not (looks_like_pdf(content) or "application/pdf" in ctype):
        return None
    if len(content) < min_bytes:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename_from_url(url)
    n = 2
    while dest.exists():
        dest = dest_dir / f"{dest.stem.rstrip('-0123456789') or 'artigo'}-{n}.pdf"
        n += 1
    dest.write_bytes(content)
    return dest


def resolve_pmcid(pmid: str, session, api_key: Optional[str] = None, timeout: int = 30) -> Optional[str]:
    """PMID -> PMCID (se o artigo tiver versão no PMC). Retorna 'PMC123...' ou None."""
    params = {"dbfrom": "pubmed", "db": "pmc", "id": pmid, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    try:
        resp = session.get(f"{EUTILS}/elink.fcgi", params=params, timeout=timeout,
                           headers={"User-Agent": BROWSER_UA})
        data = resp.json()
    except Exception:
        return None
    try:
        linksets = data.get("linksets", [])
        for ls in linksets:
            for db in ls.get("linksetdbs", []):
                if db.get("dbto") == "pmc" and db.get("links"):
                    return f"PMC{db['links'][0]}"
    except Exception:
        return None
    return None


def pmc_pdf_url(pmcid: str, session, timeout: int = 30) -> Optional[str]:
    """Acha a URL do PDF na página do PMC via meta `citation_pdf_url`."""
    page = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
    try:
        resp = session.get(page, timeout=timeout, headers={"User-Agent": BROWSER_UA})
        html = resp.text
    except Exception:
        return None
    m = re.search(r'<meta[^>]+name="citation_pdf_url"[^>]+content="([^"]+)"', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content="([^"]+)"[^>]+name="citation_pdf_url"', html, re.I)
    if not m:
        return None
    href = m.group(1).replace("&amp;", "&")
    if href.startswith("/"):
        href = "https://www.ncbi.nlm.nih.gov" + href
    return href


def try_pubmed_open_access(
    url: str, dest_dir: Path, session, api_key: Optional[str] = None
) -> tuple[Optional[Path], str]:
    """Best-effort: PMID -> PMC -> PDF. Retorna (caminho|None, motivo)."""
    pmid = extract_pmid(url)
    if not pmid:
        return None, "URL PubMed sem PMID reconhecível"
    pmcid = resolve_pmcid(pmid, session, api_key=api_key)
    time.sleep(0.4)  # respeita rate limit do NCBI
    if not pmcid:
        return None, "Sem versão open-access no PMC"
    pdf_url = pmc_pdf_url(pmcid, session)
    if not pdf_url:
        return None, f"{pmcid} sem citation_pdf_url"
    path = download_pdf(pdf_url, dest_dir, session, referer=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/")
    if not path:
        return None, f"{pmcid} encontrado mas PDF bloqueado/indisponível"
    return path, f"baixado de {pmcid}"


def try_pmc_url(url: str, dest_dir: Path, session) -> tuple[Optional[Path], str]:
    """Baixa a partir de uma URL do PMC. Se já for um .pdf, baixa direto;
    se for a página do artigo (PMC123/), resolve o citation_pdf_url."""
    if url.lower().split("?", 1)[0].rstrip("/").endswith(".pdf"):
        path = download_pdf(url, dest_dir, session)
        return (path, "baixado") if path else (None, "PDF direto do PMC indisponível")
    pmcid = pmcid_from_url(url)
    if not pmcid:
        return None, "URL PMC sem PMCID reconhecível"
    pdf_url = pmc_pdf_url(pmcid, session)
    if not pdf_url:
        return None, f"{pmcid} sem citation_pdf_url"
    path = download_pdf(pdf_url, dest_dir, session, referer=url)
    if not path:
        return None, f"{pmcid} encontrado mas PDF bloqueado/indisponível"
    return path, f"baixado de {pmcid}"
