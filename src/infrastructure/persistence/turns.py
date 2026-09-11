from __future__ import annotations

import sqlite3
from datetime import datetime
from functools import partial
from threading import Lock

from application.turns import TurnEvent
from infrastructure.persistence._worker import run_sqlite


class SQLiteTurnEvents:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()

    def record(self, event: TurnEvent) -> None:
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
