"""Real captures only: intentionally fails clearly until recording is available."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.golden.runner import run


@pytest.mark.slow
@pytest.mark.eval
@pytest.mark.asyncio
async def test_extraction_against_recorded_corpus() -> None:
    directory = Path(os.environ.get("LLM_EVAL_FIXTURES", "tests/fixtures/llm-evaluation"))
    mode = os.environ.get("LLM_EVAL_MODE", "replay")
    report = await run(mode=mode, directory=directory)
    assert report["total"] == 2500, "Portão exige corpus completo; gravação parcial não é baseline"
    thresholds_path = directory / "thresholds.json"
    assert thresholds_path.is_file(), "Limiares ausentes: registrar após medição real e revisão"
    thresholds = json.loads(thresholds_path.read_text())
    assert thresholds["source"] == "measured_real_recording"
    assert report["idade_accuracy"] >= thresholds["idade_accuracy"]
    assert report["veiculo_ano_accuracy"] >= thresholds["veiculo_ano_accuracy"]
    assert report["cep_integer_responses"] == 0
    assert report["private_cep_audit"]["correct"] == 2500
