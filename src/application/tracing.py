from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class Correlation:
    trace_id: str
    conversation_id: str


_TURN: ContextVar[Correlation | None] = ContextVar("turn_correlation", default=None)


@contextmanager
def turn_correlation(trace_id: str, conversation_id: str) -> Iterator[Correlation]:
    """Escopo do turno: a cadeia de cotação herda o trace_id e a conversa do grafo."""
    correlation = Correlation(trace_id, conversation_id)
    token = _TURN.set(correlation)
    try:
        yield correlation
    finally:
        _TURN.reset(token)


def current_turn() -> Correlation | None:
    return _TURN.get()


@dataclass(slots=True)
class WireObservation:
    correlation: Correlation
    tentativa: int
    hedge: bool
    http_status: int | None = None


class CorrelationProvider(Protocol):
    def logical(self) -> AbstractContextManager[Correlation]: ...
    def physical(self) -> AbstractContextManager[WireObservation]: ...
    def observe_http(self, status: int) -> None: ...


@dataclass(frozen=True, slots=True)
class QuoteAttempt:
    trace_id: str
    conversation_id: str
    fingerprint: str
    tentativa: int  # zero = logical resolution; positive = physical start order
    status: Literal["quoted", "declined", "unavailable", "contract_error"]
    origem: Literal["api", "cache", "regra_local"]
    http_status: int | None
    latencia_ms: int
    hedge: bool
    ano_normalizado: bool
    erro: str | None
    criado_em: datetime


class TraceReader(Protocol):
    async def read(self, trace_id: str) -> tuple[QuoteAttempt, ...]: ...


class ConversationAttemptsReader(Protocol):
    async def read_conversation(self, conversation_id: str) -> tuple[QuoteAttempt, ...]: ...
