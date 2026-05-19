#!/usr/bin/env python3
"""
Extrai páginas de livros de ginecologia por um ou mais temas.

Fluxo:
1) Lê o índice em markdown gerado em
   `*-sumario-paginas.md`.
2) Faz busca flexível do tema (ignora acentos, caixa e pontuação).
3) Cria um PDF com as páginas dos temas mais compatíveis.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from pypdf import PdfReader, PdfWriter


BOOK_PRESETS = {
    "tratado": {
        "pdf": "livros/tratado-de-ginecologia-da-febrasgo.pdf",
        "indice": "livros/tratado-de-ginecologia-da-febrasgo-sumario-paginas.md",
    },
    "williams": {
        "pdf": "livros/Williams Ginecologia.pdf",
        "indice": "livros/williams-ginecologia-sumario-paginas.md",
    },
}


@dataclass
class Entry:
    title: str
    start: int
    end: int
    depth: int


ENTRY_RE = re.compile(
    r"^(?P<indent>\s*)-\s+\*\*(?P<title>.+?)\*\*\s+—\s+pág\.\s+(?:PDF\s+)?(?P<pages>\d+(?:-\d+)?)(?:\s+\(.*\))?\s*$"
)


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> set[str]:
    stopwords = {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "em",
        "na",
        "no",
        "nas",
        "nos",
        "a",
        "o",
        "as",
        "os",
        "para",
        "com",
        "por",
        "ao",
        "aos",
        "uma",
        "um",
    }
    return {tok for tok in normalize(text).split() if tok and tok not in stopwords}


def parse_page_interval(pages: str) -> tuple[int, int]:
    if "-" in pages:
        start_str, end_str = pages.split("-", 1)
        return int(start_str), int(end_str)
    p = int(pages)
    return p, p


def load_index(index_path: Path) -> list[Entry]:
    entries: list[Entry] = []
    with index_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            match = ENTRY_RE.match(line)
            if not match:
                continue
            indent = match.group("indent")
            depth = len(indent) // 2
            title = match.group("title").strip()
            start, end = parse_page_interval(match.group("pages"))
            entries.append(Entry(title=title, start=start, end=end, depth=depth))
    if not entries:
        raise ValueError(f"Nenhuma entrada de índice encontrada em: {index_path}")
    return entries


def base_synonym_rules(query_norm: str) -> str:
    rules = {
        "leiomiomatose": "mioma uterino",
        "sua": "sangramento uterino anormal",
        "dds": "disturbios do desenvolvimento sexual",
        "hho": "eixo hipotalamo hipofise gonadal",
        "larc": "metodos anticoncepcionais reversiveis de longa duracao",
        "itu": "infeccao do trato urinario",
    }
    expanded = query_norm
    for src, dst in rules.items():
        if src in expanded and dst not in expanded:
            expanded = f"{expanded} {dst}"
    return expanded


def score_entry(query: str, entry: Entry) -> float:
    q_norm = normalize(query)
    q_norm = base_synonym_rules(q_norm)
    t_norm = normalize(entry.title)

    q_tokens = tokenize(q_norm)
    t_tokens = tokenize(t_norm)

    overlap = 0.0
    if q_tokens:
        overlap = len(q_tokens & t_tokens) / len(q_tokens)

    ratio = SequenceMatcher(None, q_norm, t_norm).ratio()

    contains_bonus = 0.0
    if q_norm and q_norm in t_norm:
        contains_bonus += 0.25
    if t_norm and t_norm in q_norm:
        contains_bonus += 0.15

    section_penalty = 0.04 if entry.depth == 0 else 0.0

    score = (0.58 * overlap) + (0.42 * ratio) + contains_bonus - section_penalty
    return max(0.0, min(1.0, score))


def rank_entries(query: str, entries: list[Entry]) -> list[tuple[Entry, float]]:
    ranked = [(entry, score_entry(query, entry)) for entry in entries]
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def safe_filename(text: str) -> str:
    n = normalize(text)
    n = n.replace(" ", "-")
    n = re.sub(r"-+", "-", n).strip("-")
    return n or "tema"


def extract_pages(pdf_path: Path, entries: list[Entry], output_path: Path, dedupe: bool) -> int:
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    for entry in entries:
        if entry.start < 1 or entry.end > total_pages:
            raise ValueError(
                f"Intervalo {entry.start}-{entry.end} fora do total de páginas do PDF ({total_pages})."
            )

    writer = PdfWriter()
    seen_pages: set[int] = set()
    added = 0
    for entry in entries:
        for p in range(entry.start, entry.end + 1):
            if dedupe and p in seen_pages:
                continue
            writer.add_page(reader.pages[p - 1])  # pypdf usa base 0
            seen_pages.add(p)
            added += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)
    return added


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extrai páginas de um ou mais temas em livros suportados, usando índices markdown com intervalos."
        )
    )
    parser.add_argument(
        "--livro",
        choices=sorted(BOOK_PRESETS.keys()),
        default="tratado",
        help=(
            "Livro preset para definir PDF e índice automaticamente. "
            "Padrão: tratado."
        ),
    )
    parser.add_argument(
        "--tema",
        action="append",
        default=[],
        help=(
            'Tema a buscar. Pode repetir a flag para múltiplos capítulos '
            '(ex.: --tema "endometriose" --tema "dor pélvica crônica").'
        ),
    )
    parser.add_argument(
        "--pdf",
        default=None,
        help="Caminho do PDF fonte. Se omitido, usa o preset definido em --livro.",
    )
    parser.add_argument(
        "--indice",
        default=None,
        help="Caminho do índice markdown. Se omitido, usa o preset definido em --livro.",
    )
    parser.add_argument(
        "--saida",
        default=None,
        help=(
            "Caminho do PDF de saída. Se omitido, gera em livros/extracoes/"
            " com nome baseado no(s) tema(s) selecionado(s)."
        ),
    )
    parser.add_argument(
        "--limiar",
        type=float,
        default=0.42,
        help="Limiar mínimo de confiança para extração automática. Padrão: 0.42.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=8,
        help="Quantidade de sugestões mostradas quando não atingir o limiar. Padrão: 8.",
    )
    parser.add_argument(
        "--listar",
        action="store_true",
        help="Lista todas as entradas disponíveis no índice e sai.",
    )
    parser.add_argument(
        "--permitir-sobreposicao",
        action="store_true",
        help="Mantém páginas repetidas quando dois temas compartilham intervalos.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    preset = BOOK_PRESETS[args.livro]
    pdf_path = Path(args.pdf if args.pdf else preset["pdf"])
    index_path = Path(args.indice if args.indice else preset["indice"])

    if not pdf_path.exists():
        print(f"Erro: PDF não encontrado: {pdf_path}", file=sys.stderr)
        return 1
    if not index_path.exists():
        print(f"Erro: índice não encontrado: {index_path}", file=sys.stderr)
        return 1

    entries = load_index(index_path)

    if args.listar:
        for e in entries:
            prefix = "  " * e.depth
            pages = f"{e.start}-{e.end}" if e.start != e.end else f"{e.start}"
            print(f"{prefix}- {e.title} ({pages})")
        return 0

    if not args.tema:
        parser.error("informe ao menos um --tema ou use --listar")

    selected_entries: list[Entry] = []
    selected_scores: list[float] = []
    selected_queries: list[str] = []

    for query in args.tema:
        ranked = rank_entries(query, entries)
        best, score = ranked[0]
        if score < args.limiar:
            print(
                f"Nenhuma correspondência com confiança suficiente para '{query}'.",
                file=sys.stderr,
            )
            print(
                f"Melhor pontuação encontrada: {score:.3f} (limiar: {args.limiar:.3f})",
                file=sys.stderr,
            )
            print("\nSugestões mais próximas:", file=sys.stderr)
            for cand, cand_score in ranked[: max(1, args.top)]:
                pages = f"{cand.start}-{cand.end}" if cand.start != cand.end else f"{cand.start}"
                print(
                    f"- {cand.title} | pág. {pages} | score={cand_score:.3f}",
                    file=sys.stderr,
                )
            return 2
        selected_entries.append(best)
        selected_scores.append(score)
        selected_queries.append(query)

    if args.saida:
        output_path = Path(args.saida)
    else:
        if len(selected_entries) == 1:
            slug = safe_filename(selected_entries[0].title)
            filename = f"{slug}.pdf"
        else:
            filename = "temas-combinados.pdf"
        output_path = Path("livros/extracoes") / args.livro / filename

    added_pages = extract_pages(
        pdf_path=pdf_path,
        entries=selected_entries,
        output_path=output_path,
        dedupe=not args.permitir_sobreposicao,
    )

    print("Extração concluída com sucesso.")
    for query, best, score in zip(selected_queries, selected_entries, selected_scores):
        pages_text = f"{best.start}-{best.end}" if best.start != best.end else f"{best.start}"
        print(f"- Tema solicitado : {query}")
        print(f"  Tema selecionado: {best.title}")
        print(f"  Páginas         : {pages_text}")
        print(f"  Confiança       : {score:.3f}")
    print(f"Páginas no PDF final: {added_pages}")
    print(f"Arquivo gerado  : {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
