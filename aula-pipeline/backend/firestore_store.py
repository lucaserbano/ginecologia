"""Camada de acesso ao Firestore para persistir o estado das aulas.

Schema:
- Coleção `aulas` (nome configurável via FIRESTORE_COLLECTION).
- Doc id = `aula.id` (ex.: "M1_A1").
- Doc data = payload pydantic do AulaItem (todos os campos serializados
  como JSON-compatíveis: datetimes como ISO 8601 string).

Locks e transações não são usados — o sistema tem apenas 1 usuário ativo
e cada ação opera sobre 1 aula isolada.
"""
from __future__ import annotations

import logging
from typing import Optional

from schemas import AulaItem
from settings import ENABLE_FIRESTORE, FIRESTORE_COLLECTION, FIRESTORE_PROJECT_ID

logger = logging.getLogger("firestore_store")

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not ENABLE_FIRESTORE:
        return None
    try:
        from google.cloud import firestore  # type: ignore
    except Exception as exc:
        logger.warning("Firestore lib indisponível: %s", exc)
        return None
    try:
        if FIRESTORE_PROJECT_ID:
            _client = firestore.Client(project=FIRESTORE_PROJECT_ID)
        else:
            _client = firestore.Client()
    except Exception as exc:
        logger.warning("Falha ao construir Firestore client: %s", exc)
        _client = None
    return _client


def is_available() -> bool:
    return _get_client() is not None


def _coll():
    client = _get_client()
    if client is None:
        return None
    return client.collection(FIRESTORE_COLLECTION)


def list_aulas() -> list[AulaItem]:
    coll = _coll()
    if coll is None:
        return []
    out: list[AulaItem] = []
    for doc in coll.stream():
        data = doc.to_dict() or {}
        data.setdefault("id", doc.id)
        try:
            out.append(AulaItem.model_validate(data))
        except Exception as exc:
            logger.warning("Aula %s no Firestore com schema invalido: %s", doc.id, exc)
    return out


def get_aula(aula_id: str) -> Optional[AulaItem]:
    coll = _coll()
    if coll is None:
        return None
    doc = coll.document(aula_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data.setdefault("id", aula_id)
    try:
        return AulaItem.model_validate(data)
    except Exception as exc:
        logger.warning("Aula %s no Firestore com schema invalido: %s", aula_id, exc)
        return None


def upsert_aula(aula: AulaItem) -> None:
    coll = _coll()
    if coll is None:
        return
    payload = aula.model_dump(mode="json")
    # Doc id já vem do aula.id; não duplicamos no payload (mas mantemos
    # pra consultas serem self-contained).
    coll.document(aula.id).set(payload)


def upsert_many(aulas: list[AulaItem]) -> int:
    coll = _coll()
    if coll is None or not aulas:
        return 0
    client = _get_client()
    batch = client.batch()
    written = 0
    for i, aula in enumerate(aulas, start=1):
        batch.set(coll.document(aula.id), aula.model_dump(mode="json"))
        written += 1
        # Firestore aceita até 500 ops por batch.
        if i % 400 == 0:
            batch.commit()
            batch = client.batch()
    batch.commit()
    return written


def delete_aula(aula_id: str) -> None:
    coll = _coll()
    if coll is None:
        return
    coll.document(aula_id).delete()


def count_aulas() -> int:
    coll = _coll()
    if coll is None:
        return 0
    # `count()` aggregation existe na nova lib; cai pra stream-count se falhar.
    try:
        agg = coll.count()
        snap = agg.get()
        return int(snap[0][0].value)
    except Exception:
        return sum(1 for _ in coll.stream())
