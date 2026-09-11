from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from application.tracing import QuoteAttempt
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.connection import apply_schema, connect


@pytest.mark.asyncio
async def test_schema_migrates_cache_and_trace_is_ordered() -> None:
    conn = connect(":memory:")
    try:
        apply_schema(conn)
        assert conn.execute("PRAGMA foreign_key_list(quote_attempts)").fetchall() == []
        assert "idx_attempts_trace" in {
            row[1] for row in conn.execute("PRAGMA index_list(quote_attempts)")
        }
        store = SQLiteAttempts(conn)
        event = QuoteAttempt(
            "trace-1",
            "conv-1",
            "a" * 64,
            1,
            "unavailable",
            "api",
            503,
            25,
            False,
            True,
            "QuoteUnavailable",
            datetime(2026, 9, 11, tzinfo=UTC),
        )
        for sequence in (3, 1, 0, 2):
            await store.record(replace(event, tentativa=sequence))
        rows = await store.read("trace-1")
        assert [r.tentativa for r in rows] == [1, 2, 3, 0]
        assert rows[0] == event
        assert await store.read("other") == ()
        assert conn.execute("SELECT count(*) FROM quote_cache").fetchone()[0] == 0
    finally:
        conn.close()
