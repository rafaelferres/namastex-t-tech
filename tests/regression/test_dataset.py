from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from domain.acceptance import AcceptanceRules
from interfaces.replay import dataset_path, read_rows
from tests.golden.harness import Case, NullExtractor, cases_from_rows, evaluate
from tests.regression.oracle import negative_cases, stratified_sample

FIXTURES = Path(__file__).parents[1] / "fixtures/evaluation"


def test_sanitized_sample_is_stratified(plans_payload: dict[str, Any]) -> None:
    data = json.loads((FIXTURES / "sample.json").read_text())
    cases = tuple(Case(**{**case, "messages": tuple(case["messages"])}) for case in data)
    rules = AcceptanceRules.from_api(plans_payload)
    assert len(cases) > 20
    assert 0 < len(negative_cases(cases, rules)) < len(cases)
    assert stratified_sample(cases, rules) == cases
    assert {case.reference_date[:4] for case in cases} == {"2026"}


@pytest.mark.asyncio
async def test_fast_sample_uses_same_extraction_metrics() -> None:
    data = json.loads((FIXTURES / "sample.json").read_text())
    cases = tuple(Case(**{**case, "messages": tuple(case["messages"])}) for case in data)
    report = await evaluate(cases, NullExtractor())
    assert report.total == 48
    assert report.idade_accuracy == report.veiculo_accuracy == 0.0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_full_dataset_oracle_and_offline_harness(plans_payload: dict[str, Any]) -> None:
    path = dataset_path()
    if not path.is_file():
        pytest.skip("Corpus local ausente: configure AUTOSEGURO_DATASET; 2500 casos não avaliados")
    rows = read_rows(path)
    cases = cases_from_rows(rows)
    rules = AcceptanceRules.from_api(plans_payload)
    assert len(rows) == 26470
    assert len(cases) == 2500
    assert {case.reference_date[:4] for case in cases} == {"2026"}
    assert len(negative_cases(cases, rules)) == 751
    report = await evaluate(cases, NullExtractor())
    assert report.total == 2500
    assert report.idade_accuracy == report.veiculo_accuracy == 0.0
    sample = json.loads((FIXTURES / "sample.json").read_text())
    assert [case.conversation_id for case in stratified_sample(cases, rules)] == [
        case["conversation_id"] for case in sample
    ]
