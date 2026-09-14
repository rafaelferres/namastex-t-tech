from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from application.external import StartupCheckError
from application.llm import LLMRole

if TYPE_CHECKING:
    from agent.graph import TurnConfig

logger = logging.getLogger(__name__)

# Pisos medidos: fonte única da verificação de partida. O `.env.example` é conferido contra
# ENV_DEFAULTS e contra estes pisos em tests/unit/test_configuration.py.
LLM_P999_SECONDS = 6.9  # D-038: p99.9 do extrator em 7.499 chamadas isoladas
# Máximo medido: 10.095 na rodada da tarefa 13 (task13-e2e.json); D-034 mediu 9.510.
MAX_CONVERSATION_TOKENS = 10095

ENV_DEFAULTS: dict[str, str] = {
    "LLM_EXTRACTOR_MODEL": "openai/gpt-4.1-mini",
    "LLM_CONVERSATION_MODEL": "openai/gpt-4.1",
    # Único aceito com imagem e áudio sob schema strict na sondagem da tarefa 10 (D-037).
    "LLM_MEDIA_MODEL": "google/gemini-2.5-flash",
    # Teto no p99.9 por chamada, com um retry (D-038); D-034 usava 4,5 s.
    "LLM_TIMEOUT_SECONDS": "7.0",
    "LLM_BUDGET_SECONDS": "7.0",
    "LLM_CONVERSATION_TOKEN_LIMIT": "16000",
}


@dataclass(frozen=True, slots=True)
class LLMConfig:
    api_key: str = field(repr=False)
    extractor_model: str = ENV_DEFAULTS["LLM_EXTRACTOR_MODEL"]
    conversation_model: str = ENV_DEFAULTS["LLM_CONVERSATION_MODEL"]
    media_model: str = ENV_DEFAULTS["LLM_MEDIA_MODEL"]
    timeout_seconds: float = float(ENV_DEFAULTS["LLM_TIMEOUT_SECONDS"])
    budget_seconds: float = float(ENV_DEFAULTS["LLM_BUDGET_SECONDS"])
    conversation_token_limit: int = int(ENV_DEFAULTS["LLM_CONVERSATION_TOKEN_LIMIT"])

    def __post_init__(self) -> None:
        if (
            not self.api_key.strip()
            or not self.extractor_model.strip()
            or not self.conversation_model.strip()
            or not self.media_model.strip()
            or self.extractor_model == self.conversation_model
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or not math.isfinite(self.budget_seconds)
            or self.budget_seconds <= 0
            or self.timeout_seconds > self.budget_seconds
            or self.conversation_token_limit <= 0
        ):
            raise ValueError("Configuração LLM inválida")

    def model_for(self, role: LLMRole) -> str:
        if role == LLMRole.MEDIA:
            return self.media_model
        return self.extractor_model if role == LLMRole.EXTRACTOR else self.conversation_model

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMConfig:
        env = os.environ if environ is None else environ
        values = dict(ENV_DEFAULTS)
        for name, default in ENV_DEFAULTS.items():
            if name in env:
                values[name] = env[name]
            else:
                logger.warning("config_padrao %s ausente; usando %s do .env.example", name, default)
        try:
            return cls(
                api_key=env.get("OPENROUTER_API_KEY", ""),
                extractor_model=values["LLM_EXTRACTOR_MODEL"],
                conversation_model=values["LLM_CONVERSATION_MODEL"],
                media_model=values["LLM_MEDIA_MODEL"],
                timeout_seconds=float(values["LLM_TIMEOUT_SECONDS"]),
                budget_seconds=float(values["LLM_BUDGET_SECONDS"]),
                conversation_token_limit=int(values["LLM_CONVERSATION_TOKEN_LIMIT"]),
            )
        except ValueError:
            raise ValueError("Configuração LLM inválida") from None


def verify_configuration(llm: LLMConfig, turn: TurnConfig) -> None:
    """Coerência contra os pisos medidos, antes de qualquer rede; conectividade é outra etapa."""
    failures: dict[str, str] = {}
    # LLM_BUDGET_SECONDS >= LLM_TIMEOUT_SECONDS já é invariante do LLMConfig: um piso cobre os dois.
    if llm.timeout_seconds < LLM_P999_SECONDS:
        failures["LLM_TIMEOUT_SECONDS"] = (
            f"configurado {llm.timeout_seconds:g} s, abaixo do p99.9 medido de "
            f"{LLM_P999_SECONDS:g} s (D-038)"
        )
    stages = turn.extraction_seconds + turn.conversation_seconds + turn.quote_seconds
    if turn.budget_seconds < stages:
        failures["orcamento_do_turno"] = (
            f"configurado {turn.budget_seconds:g} s, abaixo da soma dos tetos das etapas, "
            f"{stages:g} s"
        )
    if llm.conversation_token_limit < MAX_CONVERSATION_TOKENS:
        failures["LLM_CONVERSATION_TOKEN_LIMIT"] = (
            f"configurado {llm.conversation_token_limit}, abaixo do máximo medido de "
            f"{MAX_CONVERSATION_TOKENS} tokens por conversa (D-034)"
        )
    if failures:
        raise StartupCheckError(failures)
