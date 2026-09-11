"""Contratos pequenos para os futuros decorators e adapters."""

from __future__ import annotations

from datetime import date, datetime
from time import monotonic
from typing import Protocol

from application.tracing import QuoteAttempt
from domain.acceptance import AcceptanceRules
from domain.quote import QuoteOutcome, QuoteRequest


class QuoteProvider(Protocol):
    async def quote(self, req: QuoteRequest) -> QuoteOutcome: ...


class QuoteCache(Protocol):
    async def get(self, fingerprint: str) -> QuoteOutcome | None: ...

    async def set(self, fingerprint: str, outcome: QuoteOutcome, expires_at: datetime) -> None: ...


class AcceptanceRulesProvider(Protocol):
    async def current(self) -> AcceptanceRules | None: ...


class AttemptRecorder(Protocol):
    """Entrega não bloqueante; persistência e drenagem pertencem ao adapter."""

    def record(self, attempt: QuoteAttempt) -> None: ...

    async def finish(self, trace_id: str) -> None: ...


class Clock(Protocol):
    def today(self) -> date: ...

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    """Calendário local (como a API) e duração independente de ajustes de relógio."""

    def today(self) -> date:
        return self.now().date()

    def now(self) -> datetime:
        return datetime.now()

    def monotonic(self) -> float:
        return monotonic()
