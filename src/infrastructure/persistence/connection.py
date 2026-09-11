from __future__ import annotations

import sqlite3
from pathlib import Path


def apply_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(Path(__file__).with_name("schema.sql").read_text())


def connect(path: str | Path) -> sqlite3.Connection:
    """Startup síncrono; quem cria a conexão é responsável por fechá-la."""
    connection = sqlite3.connect(path, check_same_thread=False)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        apply_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection
