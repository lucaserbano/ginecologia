#!/usr/bin/env python3
"""Runner local da Fase 1 — "Download de todos".

Roda na máquina do Lucas (precisa do agent-browser logado no UpToDate). Faz
polling no backend por jobs de download de PDFs, baixa as referências de cada
aula (UpToDate via baixar_uptodate.py; PDFs diretos e PMC open-access via HTTP)
e sobe os arquivos para a subpasta 03_pdfs_artigos do Drive da aula, usando os
endpoints que já existem no backend.

Uso:
  # loop contínuo (deixe rodando em um terminal):
  python3 runner/runner.py

  # processa os jobs pendentes uma vez e sai:
  python3 runner/runner.py --once

  # força o download de uma aula específica (enfileira + processa):
  python3 runner/runner.py --aula M10_A1

Config por variáveis de ambiente (todas com default sensato):
  BACKEND_URL      (default: produção no Cloud Run)
  UPTODATE_SCRIPT  (default: ~/agent-browser-automations/baixar_uptodate.py)
  POLL_INTERVAL    (segundos entre polls; default 15)
  NCBI_API_KEY     (opcional; acelera/eleva o rate limit do PubMed/PMC)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import requests

import pdf_sources as pdfsrc
import browser_pdf

BACKEND_URL = os.environ.get(
    "BACKEND_URL", "https://gineco-api-468351448933.us-central1.run.app"
).rstrip("/")
UPTODATE_SCRIPT = Path(
    os.environ.get("UPTODATE_SCRIPT", str(Path.home() / "agent-browser-automations" / "baixar_uptodate.py"))
)
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "15"))
NCBI_API_KEY = os.environ.get("NCBI_API_KEY") or None
PDFS_SUBFOLDER = "03_pdfs_artigos"
# Por padrão usa o agent-browser (navegador real) para PMC/diretrizes/PDFs
# diretos — fura bloqueios de bot que o HTTP simples toma. Setar 0 volta ao HTTP.
USE_BROWSER_PDF = os.environ.get("USE_BROWSER_PDF", "1") not in ("0", "false", "False")

session = requests.Session()

session = requests.Session()


def log(msg: str) -> None:
    print(f"[runner] {msg}", flush=True)


# --- chamadas ao backend ---------------------------------------------------

def get_pending_jobs() -> list[dict]:
    r = session.get(f"{BACKEND_URL}/api/jobs/pendentes", timeout=30)
    r.raise_for_status()
    return [j for j in r.json().get("jobs", []) if j.get("status") == "pendente"]


def get_links(aula_id: str) -> list[dict]:
    r = session.get(f"{BACKEND_URL}/api/aulas/{aula_id}/links", timeout=60)
    r.raise_for_status()
    return r.json().get("links", [])


def enqueue_job(aula_id: str) -> None:
    r = session.post(f"{BACKEND_URL}/api/aulas/{aula_id}/job/download-pdfs", timeout=30)
    r.raise_for_status()


def update_job(aula_id: str, status: str, mensagem: str = None,
               baixados: list[str] = None, pendentes_manuais: list[dict] = None) -> None:
    body = {"status": status}
    if mensagem is not None:
        body["mensagem"] = mensagem
    if baixados is not None:
        body["baixados"] = baixados
    if pendentes_manuais is not None:
        body["pendentes_manuais"] = pendentes_manuais
    r = session.put(f"{BACKEND_URL}/api/aulas/{aula_id}/job", json=body, timeout=30)
    r.raise_for_status()


def upload_pdf(aula_id: str, path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "application/pdf")}
            data = {"target_subfolder": PDFS_SUBFOLDER}
            r = session.post(
                f"{BACKEND_URL}/api/aulas/{aula_id}/upload-browser",
                files=files, data=data, timeout=180,
            )
        if r.status_code == 200 and r.json().get("ok"):
            return True
        log(f"  upload falhou ({r.status_code}): {r.text[:200]}")
        return False
    except Exception as exc:
        log(f"  upload exceção: {exc}")
        return False


def try_advance_status(aula_id: str) -> None:
    """Avança para 'pdfs_baixados' (só funciona se a aula estiver em
    'bibliografia_pronta'; em outros status o backend recusa e ignoramos)."""
    try:
        session.post(f"{BACKEND_URL}/api/aulas/{aula_id}/actions/marcar-pdfs-baixados", timeout=30)
    except Exception:
        pass


# --- download do UpToDate via script existente -----------------------------

def baixar_uptodate(urls: list[str], outdir: Path) -> list[Path]:
    """Roda baixar_uptodate.py para a lista de URLs e devolve os PDFs gerados.
    Como `outdir` é exclusivo desta aula/execução, qualquer *.pdf criado ali é
    resultado desta chamada."""
    if not urls:
        return []
    if not UPTODATE_SCRIPT.exists():
        log(f"  AVISO: script do UpToDate não encontrado em {UPTODATE_SCRIPT}; pulando UpToDate.")
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    urls_file = outdir / "_uptodate_urls.txt"
    urls_file.write_text("\n".join(urls))
    log(f"  UpToDate: {len(urls)} link(s) → {UPTODATE_SCRIPT.name}")
    proc = subprocess.run(
        [sys.executable, str(UPTODATE_SCRIPT), str(urls_file), "--outdir", str(outdir), "--delay", "3"],
        text=True, capture_output=True, timeout=60 * 30,
    )
    if proc.returncode not in (0, 1):  # 1 = algumas falhas; ainda pode ter PDFs
        log(f"  UpToDate retornou {proc.returncode}: {proc.stderr[-400:]}")
    return sorted(p for p in outdir.glob("uptodate-*.pdf"))


# --- orquestração por aula --------------------------------------------------

def baixar_referencia(l: dict, workdir: Path) -> tuple[Optional[Path], str]:
    """Baixa uma referência conforme o `kind`. Com USE_BROWSER_PDF (default),
    usa o agent-browser (navegador real) para PMC/diretrizes/PDFs diretos —
    fura bloqueios de bot. Senão, cai para o download via HTTP."""
    kind, url = l["kind"], l["url"]
    browser = USE_BROWSER_PDF and browser_pdf.is_available()

    if kind == "pmc":
        if browser:
            return browser_pdf.grab_html_as_pdf(url, workdir)
        return pdfsrc.try_pmc_url(url, workdir, session)

    if kind == "pdf_direto":
        if browser:
            return browser_pdf.grab_pdf_resource(url, workdir)
        path = pdfsrc.download_pdf(url, workdir, session)
        return (path, "via HTTP") if path else (None, "PDF direto indisponível")

    if kind == "pubmed":
        # Resolve PMID -> PMC (eutils não é bloqueado) e abre a página do PMC.
        if browser:
            pmid = pdfsrc.extract_pmid(url)
            pmcid = pdfsrc.resolve_pmcid(pmid, session, api_key=NCBI_API_KEY) if pmid else None
            if not pmcid:
                return None, "Sem versão open-access no PMC"
            return browser_pdf.grab_html_as_pdf(f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/", workdir)
        return pdfsrc.try_pubmed_open_access(url, workdir, session, api_key=NCBI_API_KEY)

    # outro: páginas HTML de diretrizes/consensos — antes ia direto para manual;
    # agora tenta imprimir a página via browser (com guarda contra paywall).
    if browser:
        return browser_pdf.grab_html_as_pdf(url, workdir)
    return None, "Fonte sem download automático"


def processar_aula(aula_id: str) -> None:
    log(f"Processando {aula_id}…")
    update_job(aula_id, "em_andamento", mensagem="Coletando referências…")

    links = get_links(aula_id)
    baixados: list[Path] = []
    pendentes: list[dict] = []

    workdir = Path(tempfile.mkdtemp(prefix=f"dl_{aula_id}_"))
    try:
        uptodate_urls = [l["url"] for l in links if l["kind"] == "uptodate"]
        ut_pdfs = baixar_uptodate(uptodate_urls, workdir)
        baixados.extend(ut_pdfs)
        # UpToDate que não viraram PDF (falha de acesso) entram como manuais.
        if len(ut_pdfs) < len(uptodate_urls):
            faltam = len(uptodate_urls) - len(ut_pdfs)
            log(f"  UpToDate: {len(ut_pdfs)}/{len(uptodate_urls)} baixados ({faltam} para revisar)")

        for l in links:
            if l["kind"] in ("uptodate", "drive"):
                continue  # uptodate já tratado; drive = capítulos já no Drive
            path, motivo = baixar_referencia(l, workdir)
            if path:
                baixados.append(path)
            else:
                pendentes.append({"title": l.get("title", ""), "url": l["url"],
                                  "source": l.get("source", ""), "motivo": motivo})

        # Sobe para o Drive.
        enviados: list[str] = []
        for path in baixados:
            if upload_pdf(aula_id, path):
                enviados.append(path.name)
            else:
                pendentes.append({"title": path.name, "url": "", "source": "",
                                  "motivo": "Baixado, mas upload ao Drive falhou"})

        msg = f"{len(enviados)} PDF(s) no Drive; {len(pendentes)} para revisar manualmente."
        update_job(aula_id, "concluido", mensagem=msg, baixados=enviados, pendentes_manuais=pendentes)
        log(f"  OK: {msg}")

        # Auto-avança só se nada ficou pendente e algo foi baixado.
        if enviados and not pendentes:
            try_advance_status(aula_id)
            log("  status → tentativa de avançar para 'PDFs baixados'.")
    finally:
        # Fecha a sessão do browser de download (libera o Chrome).
        if USE_BROWSER_PDF and browser_pdf.is_available():
            try:
                browser_pdf.close()
            except Exception:
                pass
        # Limpa o diretório temporário.
        try:
            for p in workdir.glob("*"):
                p.unlink()
            workdir.rmdir()
        except Exception:
            pass


def processar_pendentes_uma_vez() -> int:
    jobs = get_pending_jobs()
    if not jobs:
        return 0
    for job in jobs:
        aula_id = job["aula_id"]
        try:
            processar_aula(aula_id)
        except Exception as exc:
            log(f"  ERRO em {aula_id}: {exc}")
            try:
                update_job(aula_id, "erro", mensagem=f"Falha no runner: {exc}")
            except Exception:
                pass
    return len(jobs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Runner local de download de PDFs (Fase 1).")
    parser.add_argument("--once", action="store_true", help="Processa os jobs pendentes uma vez e sai.")
    parser.add_argument("--aula", help="Força o download de uma aula específica (ex.: M10_A1).")
    args = parser.parse_args()

    log(f"Backend: {BACKEND_URL}")
    log(f"UpToDate script: {UPTODATE_SCRIPT} ({'ok' if UPTODATE_SCRIPT.exists() else 'NÃO ENCONTRADO'})")

    if args.aula:
        enqueue_job(args.aula)
        processar_aula(args.aula)
        return 0

    if args.once:
        n = processar_pendentes_uma_vez()
        log(f"Concluído ({n} job(s)).")
        return 0

    log(f"Polling a cada {POLL_INTERVAL:.0f}s. Ctrl+C para sair.")
    while True:
        try:
            processar_pendentes_uma_vez()
        except Exception as exc:
            log(f"poll falhou: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
