"""Migração one-shot: split do Módulo 3 de 18 -> 21 aulas no Firestore.

Contexto: o PDF 'Conteudo Pos-Gineco 2.0.pdf' lista 21 sub-temas no M3, mas o
kanban tinha 18 (A16, A17 e A18 mesclavam 2 itens cada). Este script:

  - Atualiza os docs existentes M3_A16, M3_A17, M3_A18 (novo aula_tema curto,
    aula_tema_completo, pasta_relativa) preservando status/histórico/created_at.
  - Cria os docs novos M3_A19, M3_A20, M3_A21.
  - Zera drive_folder_id/drive_subfolders das 6 aulas para que o bootstrap do
    Drive crie/relinke as subpastas com o nome correto.

É idempotente (usa upsert). A fonte dos campos é aula-pipeline/data/aulas.json,
que já reflete a estrutura de 21 aulas. O aula_tema_completo continua vindo do
overlay em runtime (store._apply_tema_completo); aqui persistimos por completude.

Uso (precisa de ADC: `gcloud auth application-default login`):

    cd aula-pipeline/backend
    python migrate_m3_split.py            # dry-run: só mostra o que faria
    python migrate_m3_split.py --apply    # grava no Firestore

Depois: redeploy do Cloud Run e POST /api/drive/bootstrap (ver handoff no README).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import firestore_store
import store
from schemas import AulaItem

ALVO_IDS = ["M3_A16", "M3_A17", "M3_A18", "M3_A19", "M3_A20", "M3_A21"]


def _seed_m3() -> dict[str, dict]:
    seed = json.loads(store.STATE_FILE.read_text(encoding="utf-8"))
    aulas = seed["aulas"] if isinstance(seed, dict) else seed
    return {a["id"]: a for a in aulas if a["id"] in ALVO_IDS}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="grava no Firestore (sem isso, dry-run)")
    args = parser.parse_args()

    if not firestore_store.is_available():
        print("ERRO: Firestore indisponível. Rode `gcloud auth application-default login` "
              "e confirme FIRESTORE_PROJECT_ID/ENABLE_FIRESTORE.", file=sys.stderr)
        return 2

    seed = _seed_m3()
    if len(seed) != len(ALVO_IDS):
        print(f"ERRO: seed não tem todos os alvos. Achou: {sorted(seed)}", file=sys.stderr)
        return 2

    for aid in ALVO_IDS:
        novo = AulaItem.model_validate(seed[aid])
        existente = None
        try:
            existente = firestore_store.get_aula(aid)
        except Exception:
            pass
        if existente is not None:
            # Preserva o que é estado vivo; sobrescreve só o que o split muda.
            novo.status = existente.status
            novo.proxima_acao = existente.proxima_acao
            novo.pendencias = existente.pendencias
            novo.historico = existente.historico
            novo.created_at = existente.created_at
            acao = "ATUALIZA"
        else:
            acao = "CRIA    "
        # Drive zerado para o bootstrap recriar/relinkar com o nome certo.
        novo.drive_folder_id = None
        novo.drive_subfolders = {}

        print(f"{acao} {aid}: aula_tema={novo.aula_tema!r}")
        print(f"           completo={novo.aula_tema_completo!r}")
        if args.apply:
            firestore_store.upsert_aula(novo)

    if args.apply:
        print("\nOK: Firestore atualizado. Agora redeploy do Cloud Run e POST /api/drive/bootstrap.")
    else:
        print("\nDRY-RUN. Rode novamente com --apply para gravar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
