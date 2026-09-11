"""Porta de inferência independente do provedor e da orquestração."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

from application.external import ConfigurationError


class LLMRole(StrEnum):
    EXTRACTOR = "extrator"
    CONVERSATION = "conversador"
    MEDIA = "midia"


@dataclass(frozen=True, slots=True)
class LLMAttachment:
    tipo: Literal["imagem", "audio"]
    formato: str  # MIME da imagem ou formato do áudio
    dados: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class LLMTool:
    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class LLMRequest:
    conversation_id: str
    role: LLMRole
    system: str
    user: str
    schema: dict[str, object]
    budget: float
    tools: tuple[LLMTool, ...] = ()
    anexos: tuple[LLMAttachment, ...] = ()


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: Decimal | None
    latency_ms: float
    tool_calls: tuple[LLMToolCall, ...] = ()


class LLMUnavailable(Exception):
    def __init__(self, *, latency_ms: float | None = None, detalhe: str | None = None) -> None:
        super().__init__("Serviço LLM indisponível")
        self.latency_ms = latency_ms
        self.detalhe = detalhe


class LLMContractError(Exception):
    def __init__(self, *, latency_ms: float | None = None, detalhe: str | None = None) -> None:
        super().__init__("Resposta LLM fora do contrato")
        self.latency_ms = latency_ms
        self.detalhe = detalhe


class LLMConfigurationError(ConfigurationError):
    """Provedor rejeitou credencial, modelo ou parâmetro; nunca vira fala de reserva."""


class TokenBudgetExceeded(Exception):
    def __init__(self) -> None:
        super().__init__("Limite de tokens da conversa atingido")


class LLMClient(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...
