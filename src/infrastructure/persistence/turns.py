from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from functools import partial
from threading import Lock

from application.turns import TurnEvent
from infrastructure.persistence._worker import run_sqlite

logger = logging.getLogger(__name__)


class SQLiteTurnEvents:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()
        self._tail: asyncio.Task[None] | None = None

    def record(self, event: TurnEvent) -> None:
        # Nunca escreve no event loop: esperar o lock aqui travava o loop inteiro até o
        # busy_timeout enquanto o checkpointer segurava uma transação entre awaits.
        previous = self._tail
        self._tail = asyncio.get_running_loop().create_task(self._write_after(previous, event))
        self._tail.add_done_callback(self._settle)

    async def _write_after(self, previous: asyncio.Task[None] | None, event: TurnEvent) -> None:
        if previous is not None:
            await asyncio.wait([previous])  # grava na ordem em que os eventos ocorreram
        await run_sqlite(partial(self._write, event))

    @staticmethod
    def _settle(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("turn_event_write_failed")

    async def drain(self) -> None:
        if self._tail is not None:
            await asyncio.wait([self._tail])

    def _write(self, event: TurnEvent) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO turn_events "
                "(id, trace_id, conversation_id, etapa, status, latencia_ms, erro, criado_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.trace_id + ":" + event.etapa,
                    event.trace_id,
                    event.conversation_id,
                    event.etapa,
                    event.status,
                    event.latencia_ms,
                    event.erro,
                    event.criado_em.isoformat(),
                ),
            )

    async def read_turn(self, trace_id: str) -> tuple[TurnEvent, ...]:
        await self.drain()  # lê as próprias escritas pendentes
        return await run_sqlite(partial(self._read, trace_id))

    def _read(self, trace_id: str) -> tuple[TurnEvent, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT trace_id, conversation_id, etapa, status, latencia_ms, erro, criado_em "
                "FROM turn_events WHERE trace_id=? ORDER BY criado_em, rowid",
                (trace_id,),
            ).fetchall()
        return tuple(
            TurnEvent(
                row[0], row[1], row[2], row[3], row[4], row[5], datetime.fromisoformat(row[6])
            )
            for row in rows
        )
