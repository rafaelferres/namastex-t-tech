from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from infrastructure.persistence.connection import connect


class CheckpointTurnStates:
    """Estado final de cada turno, lido do checkpointer; o grafo não é reexecutado."""

    def __init__(self, saver: BaseCheckpointSaver[Any]) -> None:
        self._saver = saver

    async def read_states(self, conversation_id: str) -> tuple[dict[str, Any], ...]:
        latest: dict[str, dict[str, Any]] = {}
        # alist vai do mais recente ao mais antigo: o primeiro de cada trace é o final.
        async for item in self._saver.alist({"configurable": {"thread_id": conversation_id}}):
            values = item.checkpoint["channel_values"]
            trace = values.get("trace_id")
            if isinstance(trace, str) and trace not in latest:
                latest[trace] = dict(values)
        return tuple(reversed(latest.values()))


@asynccontextmanager
async def open_turn_states(path: str | Path) -> AsyncIterator[CheckpointTurnStates]:
    """Somente leitura: inspecionar nunca escreve no banco inspecionado."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as connection:
        saver = AsyncSqliteSaver(connection)
        saver.is_setup = True  # o setup cria tabelas; num banco só leitura ele falharia
        yield CheckpointTurnStates(saver)


@asynccontextmanager
async def open_checkpointer(path: str | Path) -> AsyncIterator[AsyncSqliteSaver]:
    """Conexão dedicada no mesmo arquivo; sem ponte síncrona por chamada de grafo."""
    startup = connect(path)
    startup.close()
    async with aiosqlite.connect(path) as connection:
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.execute("PRAGMA foreign_keys=ON")
        saver = AsyncSqliteSaver(connection)
        await saver.setup()
        yield saver
