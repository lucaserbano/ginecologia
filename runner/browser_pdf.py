"""Captura de PDFs via agent-browser (navegador real).

Usado pelo runner para baixar o que o HTTP simples não consegue (PMC, diretrizes
com bot-protection). Duas estratégias:

- `grab_html_as_pdf`: abre uma PÁGINA (PMC article page, diretriz HTML) e imprime
  para PDF (texto + figuras). Ótimo para PMC open-access: dispensa o endpoint
  /pdf/ que costuma ser bloqueado.
- `grab_pdf_resource`: abre uma URL que já É um .pdf; usa o download nativo do
  navegador (Content-Disposition) e, se o PDF for só renderizado inline, imprime.

Sem login: PMC/diretrizes são abertos. Usa uma sessão/perfil próprios
(`gineco-dl`), separados do perfil logado do UpToDate.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from shutil import which
from typing import Optional

from pdf_sources import slugify

DEFAULT_SESSION = "gineco-dl"
DEFAULT_PROFILE = Path.home() / "agent-browser-automations" / "profiles" / "gineco-dl"

COOKIE_LABELS = [
    "Aceitar todos os cookies", "Accept all cookies", "Aceitar", "Accept all",
    "Aceitar Cookies", "I Agree", "Concordo", "Got it", "Reject all", "Rejeitar Todos",
]
# Sinais de página com paywall/login (evita salvar um PDF-lixo).
RESTRICTED_NEEDLES = [
    "sign in to continue", "please log in", "purchase access", "buy article",
    "subscribe to", "assine para", "faça login", "acesso restrito", "get access",
]


def is_available() -> bool:
    return which("agent-browser") is not None


def _cmd(session: str, profile: Path, *args) -> list[str]:
    profile.mkdir(parents=True, exist_ok=True)
    return ["agent-browser", "--session", session, "--profile", str(profile), *map(str, args)]


def _run(cmd: list[str], timeout: int = 120, check: bool = True) -> str:
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"agent-browser falhou: {' '.join(str(c) for c in cmd)}\n{p.stderr[-300:]}")
    return p.stdout.strip()


def _dismiss_cookies(session: str, profile: Path) -> None:
    for label in COOKIE_LABELS:
        subprocess.run(_cmd(session, profile, "find", "text", label, "click"),
                       text=True, capture_output=True, timeout=15)


def _slug_from_url(url: str) -> str:
    last = url.split("?")[0].rstrip("/").split("/")[-1] or "artigo"
    if last.lower().endswith(".pdf"):
        last = last[:-4]
    return slugify(last, "artigo")


def _dedup(path: Path) -> Path:
    n = 2
    out = path
    while out.exists():
        out = path.with_name(f"{path.stem}-{n}.pdf")
        n += 1
    return out


def _newest_pdf(dirpath: Path, since_ts: float) -> Optional[Path]:
    pdfs = [
        p for p in dirpath.glob("*.pdf")
        if p.stat().st_mtime >= since_ts and not p.name.startswith("_")
    ]
    return max(pdfs, key=lambda p: p.stat().st_mtime) if pdfs else None


def close(session: str = DEFAULT_SESSION, profile: Path = DEFAULT_PROFILE) -> None:
    subprocess.run(_cmd(session, profile, "close"), text=True, capture_output=True, timeout=30)


def grab_html_as_pdf(
    url: str,
    dest_dir: Path,
    session: str = DEFAULT_SESSION,
    profile: Path = DEFAULT_PROFILE,
    min_body_chars: int = 3_000,
    min_pdf_bytes: int = 20_000,
) -> tuple[Optional[Path], str]:
    """Abre uma página e imprime para PDF. Recusa se parecer restrita/curta."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        _run(_cmd(session, profile, "--download-path", dest_dir, "open", url), timeout=120)
        # networkidle: páginas como o PMC renderizam o conteúdo via JS DEPOIS do
        # domcontentloaded — esperar a rede assentar evita capturar a página vazia.
        _run(_cmd(session, profile, "wait", "--load", "networkidle"), timeout=90, check=False)
        _dismiss_cookies(session, profile)
        # Rede de segurança: aguarda o texto aparecer (até ~10s extras).
        body = ""
        for _ in range(4):
            body = _run(_cmd(session, profile, "eval", "document.body ? document.body.innerText : ''"),
                        timeout=60, check=False)
            if len(body) >= min_body_chars:
                break
            time.sleep(2.5)
        low = body.lower()
        if any(n in low for n in RESTRICTED_NEEDLES):
            return None, "página parece exigir login/compra"
        if len(body) < min_body_chars:
            return None, f"página curta/sem texto ({len(body)} chars)"
        out = _dedup(dest_dir / f"{_slug_from_url(url)}.pdf")
        _run(_cmd(session, profile, "pdf", out), timeout=180)
        if not out.exists() or out.stat().st_size < min_pdf_bytes:
            return None, "PDF gerado vazio/pequeno"
        return out, "via browser (HTML→PDF)"
    except Exception as exc:
        return None, f"erro no browser: {exc}"


def grab_pdf_resource(
    url: str,
    dest_dir: Path,
    session: str = DEFAULT_SESSION,
    profile: Path = DEFAULT_PROFILE,
    min_pdf_bytes: int = 20_000,
) -> tuple[Optional[Path], str]:
    """Abre uma URL que já é um .pdf: usa download nativo; se for só renderizado
    inline, imprime o visualizador."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        since = time.time()
        _run(_cmd(session, profile, "--download-path", dest_dir, "open", url), timeout=120)
        _run(_cmd(session, profile, "wait", "--load", "networkidle"), timeout=60, check=False)
        for _ in range(6):  # espera um possível download nativo
            time.sleep(1.0)
            f = _newest_pdf(dest_dir, since)
            if f and f.stat().st_size >= min_pdf_bytes:
                return f, "via browser (download)"
        out = _dedup(dest_dir / f"{_slug_from_url(url)}.pdf")
        _run(_cmd(session, profile, "pdf", out), timeout=180)
        if out.exists() and out.stat().st_size >= min_pdf_bytes:
            return out, "via browser (print)"
        return None, "PDF não capturado pelo browser"
    except Exception as exc:
        return None, f"erro no browser: {exc}"
