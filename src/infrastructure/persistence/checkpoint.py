from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from infrastructure.persistence.connection import connect


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
