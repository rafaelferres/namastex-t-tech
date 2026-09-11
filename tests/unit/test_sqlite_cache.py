from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import Mock

import pytest

from domain.quote import Declined, Quote
from infrastructure.persistence.connection import apply_schema, connect
from infrastructure.persistence.quote_cache import SQLiteQuoteCache


@pytest.mark.parametrize("memory", [True, False])
def test_connection_and_idempotent_schema(tmp_path: Path, memory: bool) -> None:
    conn = connect(":memory:" if memory else tmp_path / "cache.sqlite")
    try:
        apply_schema(conn)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == ("memory" if memory else "wal")
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("quote_cache",),
            ("quote_attempts",),
        ]
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("declined", [False, True])
async def test_persistence_survives_restart_without_precision_loss(
    tmp_path: Path,
    quote_payload: dict[str, Any],
    declined: bool,
) -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    clock = Mock(now=Mock(return_value=now))
    quote = Quote.from_api(quote_payload)
    assert quote.primeiro_pagamento_pro_rata is not None
    quote = replace(
        quote,
        premio_mensal=Decimal("313.80"),
        franquia=Decimal("0.10"),
        primeiro_pagamento_pro_rata=replace(
            quote.primeiro_pagamento_pro_rata,
            valor_primeiro_pagamento=Decimal("12345678901234567890.123456789"),
        ),
        ano_normalizado=True,
    )
    outcome = Declined("Perfil recusado", ano_normalizado=True) if declined else quote
    path = tmp_path / "cache.sqlite"
    conn = connect(path)
    cache = SQLiteQuoteCache(conn, clock)
    try:
        assert await cache.get("key") is None
        await cache.set("key", outcome, now + timedelta(hours=12))
        raw = conn.execute("SELECT outcome FROM quote_cache").fetchone()[0]
        if not declined:
            assert '"313.80"' in raw and '"0.10"' in raw
    finally:
        conn.close()
    conn = connect(path)
    try:
        cache = SQLiteQuoteCache(conn, clock)
        restored = await cache.get("key")
        assert restored == outcome
        assert restored.ano_normalizado is True
        if isinstance(restored, Quote):
            assert str(restored.premio_mensal) == "313.80"
            assert str(restored.franquia) == "0.10"
            assert (
                str(restored.primeiro_pagamento_pro_rata.valor_primeiro_pagamento)
                == "12345678901234567890.123456789"
            )
        clock.now.return_value = now + timedelta(hours=12)
        assert await cache.get("key") is None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_memory_cache_upsert_and_absent_prorata(quote_payload: dict[str, Any]) -> None:
    now = datetime(2026, 9, 11, tzinfo=UTC)
    conn = connect(":memory:")
    try:
        cache = SQLiteQuoteCache(conn, Mock(now=Mock(return_value=now)))
        await cache.set("a", Declined("Recusado"), now + timedelta(days=1))
        quote = replace(Quote.from_api(quote_payload), primeiro_pagamento_pro_rata=None)
        await cache.set("a", quote, now + timedelta(days=1))
        assert await cache.get("a") == quote
        assert await cache.get("other") is None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_cancelled_write_finishes_before_connection_can_be_closed() -> None:
    conn = connect(":memory:")
    started: Future[None] = Future()
    release = Event()
    now = datetime(2026, 9, 11, tzinfo=UTC)

    def authorize(action: int, *args: object) -> int:
        if action == sqlite3.SQLITE_INSERT:
            started.set_result(None)
            release.wait()
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorize)
    cache = SQLiteQuoteCache(conn, Mock(now=Mock(return_value=now)))
    task = asyncio.create_task(cache.set("a", Declined("Recusado"), now + timedelta(days=1)))
    try:
        await asyncio.wrap_future(started)
        task.cancel()
        cycled = asyncio.Event()
        asyncio.get_running_loop().call_soon(cycled.set)
        await cycled.wait()
        assert not task.done(), "Cancelamento não pode abandonar uma conexão ainda em uso"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Safety on the red path: never close while the original implementation runs.
        await asyncio.get_running_loop().shutdown_default_executor()
        conn.close()
