"""Coerência da configuração na partida (tarefa 13): o `.env` da tarefa 8 derrubava a CLI."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from agent.graph import TurnConfig
from application.external import StartupCheckError
from infrastructure.llm.config import (
    ENV_DEFAULTS,
    LLM_P999_SECONDS,
    MAX_CONVERSATION_TOKENS,
    LLMConfig,
    verify_configuration,
)
from infrastructure.wiring import open_live_stack

COHERENT = LLMConfig(api_key="chave-de-teste")


@pytest.mark.parametrize(
    ("llm", "turn", "variable", "configured", "measured"),
    [
        (
            replace(COHERENT, timeout_seconds=2.0, budget_seconds=2.5),
            TurnConfig(),
            "LLM_TIMEOUT_SECONDS",
            "2 s",
            f"{LLM_P999_SECONDS:g} s",
        ),
        (
            COHERENT,
            TurnConfig(budget_seconds=10.0),
            "orcamento_do_turno",
            "10 s",
            "17.5 s",
        ),
        (
            replace(COHERENT, conversation_token_limit=4000),
            TurnConfig(),
            "LLM_CONVERSATION_TOKEN_LIMIT",
            "4000",
            str(MAX_CONVERSATION_TOKENS),
        ),
    ],
)
def test_configuration_below_measured_floor_fails_loudly(
    llm: LLMConfig, turn: TurnConfig, variable: str, configured: str, measured: str
) -> None:
    with pytest.raises(StartupCheckError) as caught:
        verify_configuration(llm, turn)
    assert list(caught.value.falhas) == [variable]
    assert configured in caught.value.falhas[variable]
    assert measured in caught.value.falhas[variable]


def test_task8_values_report_every_violation_at_once() -> None:
    task8 = replace(
        COHERENT, timeout_seconds=2.0, budget_seconds=2.5, conversation_token_limit=4000
    )
    with pytest.raises(StartupCheckError) as caught:
        verify_configuration(task8, TurnConfig(6.0, 2.5, 3.0, 3.5))
    assert set(caught.value.falhas) == {
        "LLM_TIMEOUT_SECONDS",
        "LLM_CONVERSATION_TOKEN_LIMIT",
        "orcamento_do_turno",
    }


def test_coherent_configuration_passes_silently(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        verify_configuration(COHERENT, TurnConfig())
    assert caplog.records == []


def test_missing_variable_uses_example_default_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    environment = {
        "OPENROUTER_API_KEY": "chave-de-teste",
        "LLM_TIMEOUT_SECONDS": "7.5",
        "LLM_BUDGET_SECONDS": "8",
    }
    with caplog.at_level(logging.WARNING):
        config = LLMConfig.from_env(environment)
    assert config.conversation_token_limit == int(ENV_DEFAULTS["LLM_CONVERSATION_TOKEN_LIMIT"])
    assert config.timeout_seconds == 7.5
    warned = caplog.text
    assert "LLM_CONVERSATION_TOKEN_LIMIT" in warned
    assert "LLM_MEDIA_MODEL" in warned
    assert "LLM_TIMEOUT_SECONDS" not in warned
    assert "chave-de-teste" not in warned


def test_env_example_matches_code_defaults() -> None:
    example = Path(__file__).parents[2] / ".env.example"
    values = dict(
        line.split("=", 1)
        for line in example.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert {name: values.get(name) for name in ENV_DEFAULTS} == ENV_DEFAULTS
    verify_configuration(LLMConfig.from_env({**values, "OPENROUTER_API_KEY": "x"}), TurnConfig())


@pytest.mark.asyncio
async def test_live_stack_rejects_incoherent_environment_before_any_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "chave-de-teste")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "2.0")
    monkeypatch.setenv("LLM_BUDGET_SECONDS", "2.5")
    monkeypatch.setenv("LLM_CONVERSATION_TOKEN_LIMIT", "4000")
    with pytest.raises(StartupCheckError):
        # Porta 9 (discard): se a verificação não parar antes, a conexão falha com outro erro.
        async with open_live_stack(tmp_path / "db.sqlite", quote_url="http://127.0.0.1:9"):
            pass
    assert not (tmp_path / "db.sqlite").exists()
