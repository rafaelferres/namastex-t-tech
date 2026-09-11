from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from functools import partial
from threading import Lock
from uuid import uuid4

from application.tracing import QuoteAttempt
from infrastructure.persistence._worker import run_sqlite


class SQLiteAttempts:
    """Recorder e leitor em conexão dedicada, distinta da conexão do cache."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()

    async def record(self, attempt: QuoteAttempt) -> None:
        await run_sqlite(partial(self._write, attempt))

    def _write(self, event: QuoteAttempt) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO quote_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    event.trace_id,
                    event.conversation_id,
                    event.fingerprint,
                    event.tentativa,
                    event.status,
                    event.origem,
                    event.http_status,
                    event.latencia_ms,
                    int(event.hedge),
                    int(event.ano_normalizado),
                    event.erro,
                    event.criado_em.astimezone(UTC).isoformat(),
                ),
            )

    async def read(self, trace_id: str) -> tuple[QuoteAttempt, ...]:
        return await run_sqlite(partial(self._read, trace_id))

    def _read(self, trace_id: str) -> tuple[QuoteAttempt, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT trace_id, conversation_id, fingerprint, tentativa, status, origem, "
                "http_status, latencia_ms, hedge, ano_normalizado, erro, criado_em "
                "FROM quote_attempts WHERE trace_id=? "
                "ORDER BY CASE WHEN tentativa=0 THEN 1 ELSE 0 END, tentativa, criado_em, rowid",
                (trace_id,),
            ).fetchall()
        return tuple(
            QuoteAttempt(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                bool(row[8]),
                bool(row[9]),
                row[10],
                datetime.fromisoformat(row[11]),
            )
            for row in rows
        )
