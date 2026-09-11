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
        self._pending: deque[QuoteAttempt] = deque()
        self._worker: asyncio.Task[None] | None = None

    def record(self, attempt: QuoteAttempt) -> None:
        if len(self._pending) >= self._capacity:
            logger.warning("trace_buffer_full")
            return
        self._pending.append(attempt)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending:
            event = self._pending.popleft()
            try:
                await self._write(event)
            except Exception:
                logger.warning("trace_record_failed")

    async def flush(self) -> None:
        """Drenar antes de inspecionar/fechar; cancelamento aguarda recursos em uso."""
        cancelled = False
        while self._worker is not None:
            worker = self._worker
            try:
                await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                cancelled = True
                if worker.done():
                    raise
        if cancelled:
            raise asyncio.CancelledError()
