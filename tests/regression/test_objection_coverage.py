"""Roteamento de objeção sobre o corpus local: léxico antigo ("caro") contra o piso novo."""

from __future__ import annotations

from collections import defaultdict

import pytest

from application.ingest import price_objection
from domain.objection import objecao_lexical
from interfaces.replay import dataset_path, read_rows
from tests.fakes import CANONICAL_OBJECTIONS

pytestmark = pytest.mark.slow


def test_floor_routes_every_dataset_objection_without_false_positives():
    if not dataset_path().is_file():
        pytest.skip("Corpus local ausente; configure AUTOSEGURO_DATASET")
    objections: dict[str, list[str]] = defaultdict(list)
    others: list[str] = []
    for row in read_rows(dataset_path()):
        if row["sender_role"] != "lead" or row["message_type"] != "text":
            continue
        text = str(row["message_body"])
        # Gabarito independente do regex: a frase principal é uma das seis do gerador.
        if text.split("...")[0] in CANONICAL_OBJECTIONS:
            objections[str(row["conversation_id"])].append(text)
        else:
            others.append(text)
    before = sum(any(price_objection(t) for t in texts) for texts in objections.values())
    after = sum(any(objecao_lexical(t) is not None for t in texts) for texts in objections.values())
    false_positives = sum(objecao_lexical(text) is not None for text in others)
    print(
        f"\nconversas com objeção: {len(objections)}; roteadas antes: {before}; "
        f"depois: {after}; falsos positivos: {false_positives}/{len(others)} mensagens"
    )
    assert after == len(objections)
    assert false_positives == 0
