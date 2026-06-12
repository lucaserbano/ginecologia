#!/usr/bin/env python3
"""Geração do roteiro da aula via NotebookLM (CLI `notebooklm-py`).

Roda na máquina do Lucas, dentro do runner local — o NotebookLM precisa de
browser + sessão Google logada (conta `erbano.lho@gmail.com`), então NÃO roda no
Cloud Run. Este módulo cuida só da orquestração da CLI e do filesystem; toda a
conversa com o backend (listar/baixar fontes do Drive, colar o texto, avançar o
status) fica em `runner.py`, que chama `gerar_roteiro(...)`.

Pré-requisitos (one-time, ver runner/README.md e notebooklm-integration/SKILL.md):
  pip install "notebooklm-py[browser]"
  playwright install chromium
  notebooklm auth check      # login Google erbano.lho@gmail.com

Config por env (todas com default sensato):
  NOTEBOOKLM_BIN   (default: "notebooklm")
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

NOTEBOOKLM_BIN = os.environ.get("NOTEBOOKLM_BIN", "notebooklm")

# Diretrizes de roteirização (vira uma fonte PDF no notebook) e prompt da aula.
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "aulas" / "templates"
SYSTEM_PROMPT_MD = TEMPLATES_DIR / "system_prompt_certo.md"
PROMPT_MD = TEMPLATES_DIR / "prompt_certo.md"


class NotebookLMError(RuntimeError):
    pass


# --- invocação da CLI -------------------------------------------------------

def _run(args: list[str], log: Callable[[str], None], timeout: int = 60 * 20) -> subprocess.CompletedProcess:
    cmd = [NOTEBOOKLM_BIN, *args]
    log(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise NotebookLMError(f"`{' '.join(cmd[:3])}…` saiu {proc.returncode}: {tail}")
    return proc


def _parse_json(stdout: str) -> Optional[dict]:
    """A CLI emite JSON com `--json`, às vezes precedido por linhas de log.
    Tenta o parse direto e, falhando, procura o último objeto/array JSON."""
    stdout = (stdout or "").strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])\s*$", stdout, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _json_id(stdout: str, outer_key: str) -> Optional[str]:
    """Extrai um id da saída `--json`. Procura `data[outer_key]['id']` (ex.:
    `notebook.id`, `source.id`) e só então `data['id']`. **Nunca** cai para
    `notebook_id` — senão o id do notebook seria confundido com o da fonte (a
    saída do `add-drive` traz ambos: `{"source":{"id":…},"notebook_id":…}`)."""
    data = _parse_json(stdout)
    if isinstance(data, dict):
        inner = data.get(outer_key)
        if isinstance(inner, dict):
            v = inner.get("id")
            if isinstance(v, str) and v.strip():
                return v.strip()
        v = data.get("id")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# --- operações de alto nível ------------------------------------------------

def create_notebook(name: str, log: Callable[[str], None]) -> str:
    proc = _run(["create", name, "--json"], log)
    nb = _json_id(proc.stdout, "notebook")
    if not nb:
        raise NotebookLMError(f"Não consegui extrair o notebook id de: {proc.stdout[:300]}")
    log(f"  notebook '{name}' → {nb}")
    return nb


def add_drive_source(nb: str, file_id: str, title: str, log: Callable[[str], None]) -> Optional[str]:
    """Tenta ingerir um PDF do Drive por file ID (mesma conta Google). A CLI exige
    `FILE_ID TITLE` e, como as fontes são PDFs, `--mime-type pdf` (o default é
    google-doc). Retorna o source id, ou None se a CLI recusar (cai no fallback)."""
    try:
        proc = _run(
            ["source", "add-drive", file_id, title, "-n", nb, "--mime-type", "pdf", "--json"], log
        )
    except NotebookLMError as exc:
        log(f"  add-drive falhou ({exc}); usando fallback de download.")
        return None
    return _json_id(proc.stdout, "source")


def add_file_source(nb: str, path: Path, log: Callable[[str], None]) -> Optional[str]:
    # `path.resolve()` evita symlinks no prefixo (no macOS /var -> /private/var),
    # que a CLI recusa; `--follow-symlinks` é cinto-e-suspensório (arquivo nosso).
    proc = _run(
        ["source", "add", str(path.resolve()), "--type", "file", "--follow-symlinks",
         "-n", nb, "--json"], log
    )
    return _json_id(proc.stdout, "source")


def add_text_source(nb: str, title: str, text: str, log: Callable[[str], None]) -> Optional[str]:
    proc = _run(["source", "add", text, "--type", "text", "--title", title, "-n", nb, "--json"], log)
    return _json_id(proc.stdout, "source")


def wait_source(nb: str, source_id: Optional[str], log: Callable[[str], None]) -> None:
    """Bloqueia até a fonte ficar `ready` (CLI exige SOURCE_ID; default 120s)."""
    if not source_id:
        log("  AVISO: sem source id para aguardar; seguindo sem 'source wait'.")
        return
    try:
        _run(["source", "wait", source_id, "-n", nb, "--timeout", "600"], log, timeout=60 * 11)
    except NotebookLMError as exc:
        log(f"  AVISO: source wait falhou ({exc}); seguindo mesmo assim.")


def ask(nb: str, source_ids: list[str], prompt_file: Path, log: Callable[[str], None]) -> str:
    args = ["ask", "--new", "--yes", "--json", "-n", nb]
    for sid in source_ids:
        if sid:
            args += ["-s", sid]
    args += ["--prompt-file", str(prompt_file)]
    proc = _run(args, log, timeout=60 * 30)
    data = _parse_json(proc.stdout)
    if isinstance(data, dict):
        for k in ("answer", "text", "response", "content"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v
    # Sem JSON utilizável: devolve o stdout cru (melhor que perder o roteiro).
    if proc.stdout.strip():
        return proc.stdout.strip()
    raise NotebookLMError("`ask` não retornou texto.")


# --- PDF das diretrizes -----------------------------------------------------

def md_to_pdf(md_path: Path, out_path: Path) -> bool:
    """Converte o .md das diretrizes em PDF (texto puro). Usa fpdf2 se houver.
    Retorna False se não foi possível gerar (o chamador cai para text source)."""
    try:
        from fpdf import FPDF
    except Exception:
        return False
    try:
        text = md_path.read_text(encoding="utf-8")
        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        # Latin-1 é o charset nativo do FPDF core; remove o que não couber.
        # Largura explícita (epw) + wrapmode CHAR para não estourar em linhas longas.
        for line in text.split("\n"):
            safe = line.encode("latin-1", "replace").decode("latin-1")
            pdf.multi_cell(pdf.epw, 6, safe if safe.strip() else " ", wrapmode="CHAR")
        pdf.output(str(out_path))
        return out_path.exists()
    except Exception:
        return False


def _build_prompt(tema: str, out_path: Path) -> None:
    """Monta o prompt da aula a partir de prompt_certo.md, injetando o tema."""
    template = PROMPT_MD.read_text(encoding="utf-8")
    # `Tema da aula: [tema da aula aqui]` → tema real.
    prompt = re.sub(r"\[tema da aula aqui\]", tema, template)
    if "[tema da aula aqui]" not in template and tema not in prompt:
        # placeholder ausente: prefixa o tema para garantir.
        prompt = f"Tema da aula: {tema}\n\n{template}"
    out_path.write_text(prompt, encoding="utf-8")


# --- ponto de entrada usado pelo runner -------------------------------------

def gerar_roteiro(
    notebook_name: str,
    tema: str,
    sources: list[dict],
    download_fn: Callable[[str], bytes],
    log: Callable[[str], None] = print,
) -> tuple[str, list[str], list[dict]]:
    """Cria o notebook, sobe as fontes (Drive add-drive c/ fallback de download +
    as diretrizes em PDF), roda o prompt da aula e devolve o roteiro.

    `sources`: lista de {"id": <file_id>, "name": <nome>} dos PDFs do Drive.
    `download_fn(file_id) -> bytes`: baixa o PDF do Drive (fallback de ingestão).

    Retorna (roteiro, nomes_de_fontes_ok, fontes_que_falharam[{name,motivo}])."""
    if not SYSTEM_PROMPT_MD.exists() or not PROMPT_MD.exists():
        raise NotebookLMError(
            f"Templates ausentes: {SYSTEM_PROMPT_MD.name}/{PROMPT_MD.name} em {TEMPLATES_DIR}"
        )

    nb = create_notebook(notebook_name, log)
    source_ids: list[str] = []
    fontes_ok: list[str] = []
    fontes_falhas: list[dict] = []
    workdir = Path(tempfile.mkdtemp(prefix="nlm_"))

    try:
        # 1) PDFs das fontes (artigos + capítulos de livro) do Drive.
        for src in sources:
            file_id = src.get("id")
            if not file_id:
                continue
            name = src.get("name") or f"{file_id}.pdf"
            title = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
            try:
                sid = add_drive_source(nb, file_id, title, log)
                if not sid:
                    # fallback: baixa do Drive e sobe o arquivo.
                    data = download_fn(file_id)
                    tmp = workdir / (name if name.lower().endswith(".pdf") else f"{name}.pdf")
                    tmp.write_bytes(data)
                    sid = add_file_source(nb, tmp, log)
                wait_source(nb, sid, log)
                if sid:
                    source_ids.append(sid)
                fontes_ok.append(name)
            except Exception as exc:
                log(f"  fonte '{name}' falhou: {exc}")
                fontes_falhas.append({"name": name, "motivo": str(exc)[:200]})

        if not fontes_ok:
            raise NotebookLMError("Nenhuma fonte foi ingerida no NotebookLM.")

        # 2) Diretrizes de roteirização como fonte (PDF; fallback = text source).
        diretrizes_pdf = workdir / "diretrizes_roteirizacao.pdf"
        try:
            if md_to_pdf(SYSTEM_PROMPT_MD, diretrizes_pdf):
                sid = add_file_source(nb, diretrizes_pdf, log)
            else:
                log("  fpdf indisponível; subindo diretrizes como text source.")
                sid = add_text_source(
                    nb, "Diretrizes de Roteirização",
                    SYSTEM_PROMPT_MD.read_text(encoding="utf-8"), log,
                )
            wait_source(nb, sid, log)
            if sid:
                source_ids.append(sid)
        except Exception as exc:
            log(f"  AVISO: não consegui adicionar as diretrizes ({exc}); seguindo só com as fontes.")

        # 3) Monta o prompt e roda.
        prompt_file = workdir / "prompt.txt"
        _build_prompt(tema, prompt_file)
        roteiro = ask(nb, source_ids, prompt_file, log)
        return roteiro, fontes_ok, fontes_falhas
    finally:
        try:
            for p in workdir.glob("*"):
                p.unlink()
            workdir.rmdir()
        except Exception:
            pass
