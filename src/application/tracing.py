from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class Correlation:
    trace_id: str
    conversation_id: str


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
