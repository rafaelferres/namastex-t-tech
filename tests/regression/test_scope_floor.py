"""Piso de assunto contra o corpus: o dataset só tem leads pedindo cotação."""

from __future__ import annotations

import pytest

from domain.scope import assunto_lexical
from interfaces.replay import dataset_path, read_rows

pytestmark = pytest.mark.slow


def test_scope_floor_has_no_false_positive_on_dataset_leads():
    if not dataset_path().is_file():
        pytest.skip("Corpus local ausente; configure AUTOSEGURO_DATASET")
    rows = read_rows(dataset_path())
    lead = [str(row["message_body"]) for row in rows if row["sender_role"] == "lead"]
    assert sum(assunto_lexical(text) is not None for text in lead) == 0
