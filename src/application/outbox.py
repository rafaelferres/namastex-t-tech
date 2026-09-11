"""Ports and use case for durable handoff effects."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from application.ports import Clock
from domain.handoff import HandoffDecision

type HandoffDestination = Literal["lead", "webhook_vendas", "api_fila"]
type Sleep = Callable[[float], Awaitable[None]]

HANDOFF_DESTINATIONS: tuple[HandoffDestination, ...] = (
    "lead",
    "webhook_vendas",
    "api_fila",
)


class HandoffDeliveryError(Exception):
    """A sink failed without carrying remote content or private data."""

    def __init__(self) -> None:
        super().__init__("handoff_delivery_failed")


@dataclass(frozen=True, slots=True)
class HandoffEffect:
    identifier: str
    conversation_id: str
    destino: HandoffDestination
    decision: HandoffDecision
    tentativas: int


class HandoffSink(Protocol):
    async def emit(
        self,
        decision: HandoffDecision,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> None: ...


class HandoffOutbox(Protocol):
    async def due_handoffs(
        self, now: datetime, *, limit: int = 100
    ) -> tuple[HandoffEffect, ...]: ...

    async def mark_handoff_delivered(self, identifier: str, now: datetime) -> None: ...

    async def mark_handoff_failed(
        self,
        identifier: str,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None: ...


class HandoffDispatcher:
    """Deliver due effects independently, preserving their persisted order."""

    def __init__(
        self,
        outbox: HandoffOutbox,
        sinks: Mapping[HandoffDestination, HandoffSink],
        *,
        clock: Clock,
        retry_delays: tuple[float, ...] = (1.0, 5.0, 30.0, 300.0),
        sleep: Sleep | None = None,
        poll_interval: float | None = None,
    ) -> None:
        if set(sinks) != set(HANDOFF_DESTINATIONS):
            raise ValueError("Dispatcher exige os três destinos de handoff")
        if not retry_delays or any(delay < 0 for delay in retry_delays):
            raise ValueError("Delays de retry devem ser não negativos")
        if (sleep is None) != (poll_interval is None):
            raise ValueError("Polling exige sleep e intervalo")
        if poll_interval is not None and poll_interval <= 0:
            raise ValueError("Intervalo de polling deve ser positivo")
        self._outbox = outbox
        self._sinks = dict(sinks)
        self._clock = clock
        self._retry_delays = retry_delays
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None

    async def drain_due(self, *, limit: int = 100) -> int:
        if limit < 1:
            raise ValueError("Limite deve ser positivo")
        effects = await self._outbox.due_handoffs(self._clock.now(), limit=limit)
        for effect in effects:
            try:
                await self._sinks[effect.destino].emit(
                    effect.decision,
                    conversation_id=effect.conversation_id,
                    idempotency_key=effect.identifier,
                )
            except Exception as error:
                delay = self._retry_delays[min(effect.tentativas, len(self._retry_delays) - 1)]
                await self._outbox.mark_handoff_failed(
                    effect.identifier,
                    error=type(error).__name__,
                    next_attempt_at=self._clock.now() + timedelta(seconds=delay),
                )
            else:
                await self._outbox.mark_handoff_delivered(effect.identifier, self._clock.now())
        return len(effects)

    async def __aenter__(self) -> HandoffDispatcher:
        if self._sleep is not None and self._poll_interval is not None:
            self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        assert self._sleep is not None
        assert self._poll_interval is not None
        while True:
            await self.drain_due()
            await self._sleep(self._poll_interval)
