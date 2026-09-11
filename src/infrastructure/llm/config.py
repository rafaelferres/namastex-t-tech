from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from application.llm import LLMRole


@dataclass(frozen=True, slots=True)
class LLMConfig:
    api_key: str = field(repr=False)
    extractor_model: str = "openai/gpt-4.1-mini"
    conversation_model: str = "openai/gpt-4.1"
    timeout_seconds: float = 2.0
    budget_seconds: float = 2.5
    conversation_token_limit: int = 4000

    def __post_init__(self) -> None:
        if (
            not self.api_key.strip()
            or not self.extractor_model.strip()
            or not self.conversation_model.strip()
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
        return self.extractor_model if role == LLMRole.EXTRACTOR else self.conversation_model

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMConfig:
        env = os.environ if environ is None else environ
        try:
            return cls(
                api_key=env.get("OPENROUTER_API_KEY", ""),
                extractor_model=env.get("LLM_EXTRACTOR_MODEL", "openai/gpt-4.1-mini"),
                conversation_model=env.get("LLM_CONVERSATION_MODEL", "openai/gpt-4.1"),
                timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS", "2.0")),
                budget_seconds=float(env.get("LLM_BUDGET_SECONDS", "2.5")),
                conversation_token_limit=int(env.get("LLM_CONVERSATION_TOKEN_LIMIT", "4000")),
            )
        except ValueError:
            raise ValueError("Configuração LLM inválida") from None
