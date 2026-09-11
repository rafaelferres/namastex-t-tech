from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable

from application.tracing import QuoteAttempt

logger = logging.getLogger(__name__)


class BufferedAttemptRecorder:
    """Fila limitada: nunca disputa o orçamento de uma cotação com I/O do trace."""

    def __init__(
        self, write: Callable[[QuoteAttempt], Awaitable[None]], *, capacity: int = 1024
    ) -> None:
        if capacity < 1:
            raise ValueError("Capacidade deve ser positiva")
        self._write = write
        self._capacity = capacity
        self._pending: deque[tuple[QuoteAttempt, asyncio.Future[None]]] = deque()
        self._last: dict[str, asyncio.Future[None]] = {}
        self._worker: asyncio.Task[None] | None = None

    def record(self, attempt: QuoteAttempt) -> None:
        if len(self._pending) >= self._capacity:
            logger.warning("trace_buffer_full")
            return
        delivered: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending.append((attempt, delivered))
        self._last[attempt.trace_id] = delivered
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        cancelled = False
        while self._pending:
            event, delivered = self._pending.popleft()
            try:
                await self._write(event)
            except asyncio.CancelledError:
                cancelled = True
                logger.warning("trace_record_cancelled")
            except Exception:
                logger.warning("trace_record_failed")
            finally:
                delivered.set_result(None)
        if cancelled:
            raise asyncio.CancelledError()

    async def finish(self, trace_id: str) -> None:
        """Barreira por cotação, chamada automaticamente na fronteira lógica."""
        delivered = self._last.get(trace_id)
        if delivered is None:
            return
        cancelled = False
        while True:
            try:
                await asyncio.shield(delivered)
                break
            except asyncio.CancelledError:
                cancelled = True
                if delivered.done():
                    break
        if self._last.get(trace_id) is delivered:
            del self._last[trace_id]
        if cancelled:
            raise asyncio.CancelledError()
