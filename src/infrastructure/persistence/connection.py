from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


def apply_schema(connection: sqlite3.Connection) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text()
    # executescript commits implicitly; explicit BEGIN makes DDL and backfill atomic.
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + schema)
        if not connection.execute("PRAGMA foreign_key_list(quote_attempts)").fetchall():
            rows = connection.execute(
                "SELECT conversation_id, min(criado_em) FROM quote_attempts "
                "WHERE conversation_id NOT IN (SELECT id FROM conversations) "
                "GROUP BY conversation_id"
            ).fetchall()
            for conversation_id, created in rows:
                identity = hashlib.sha256(conversation_id.encode()).hexdigest()
                lead_id = "legacy:" + identity
                connection.execute(
                    "INSERT OR IGNORE INTO leads VALUES (?, 'legacy', ?, NULL, ?)",
                    (lead_id, identity, created),
                )
                connection.execute(
                    "INSERT INTO conversations "
                    "(id, lead_id, status, iniciada_em, atualizada_em) "
                    "VALUES (?, ?, 'encerrada', ?, ?)",
                    (conversation_id, lead_id, created, created),
                )
            start = schema.index("CREATE TABLE IF NOT EXISTS quote_attempts")
            statement = schema[start:].split(";", 1)[0]
            connection.execute(statement.replace("quote_attempts", "quote_attempts_migration", 1))
            connection.execute("INSERT INTO quote_attempts_migration SELECT * FROM quote_attempts")
            connection.execute("DROP TABLE quote_attempts")
            connection.execute("ALTER TABLE quote_attempts_migration RENAME TO quote_attempts")
            connection.execute("CREATE INDEX idx_attempts_trace ON quote_attempts(trace_id)")
        outbound_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(outbound_messages)")
        }
        if "erro" not in outbound_columns:
            connection.execute("ALTER TABLE outbound_messages ADD COLUMN erro TEXT")
        if "proxima_tentativa_em" not in outbound_columns:
            connection.execute(
                "ALTER TABLE outbound_messages ADD COLUMN proxima_tentativa_em TEXT"
            )
            connection.execute(
                "UPDATE outbound_messages SET proxima_tentativa_em=criado_em "
                "WHERE status IN ('pendente', 'falhou')"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


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
