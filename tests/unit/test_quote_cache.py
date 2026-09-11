from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from domain.quote import Declined, Quote, QuoteContractError, QuoteRequest, QuoteUnavailable
from infrastructure.persistence.connection import connect
from infrastructure.persistence.quote_cache import SQLiteQuoteCache
from infrastructure.quote.cache import CachingQuoteProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("declined", [False, True])
async def test_cache_miss_then_hit(quote_payload: dict[str, Any], declined: bool) -> None:
    now = datetime(2026, 9, 11, 23, 59, tzinfo=timezone(timedelta(hours=-3)))
    clock = Mock(now=Mock(return_value=now), today=Mock(return_value=now.date()))
    conn = connect(":memory:")
    try:
        cache = SQLiteQuoteCache(conn, clock)
        result = Declined("Recusado") if declined else Quote.from_api(quote_payload)
        inner = AsyncMock(quote=AsyncMock(return_value=result))
        provider = CachingQuoteProvider(inner, cache, clock)
        req = QuoteRequest("completo", 30, 2026, "01310100")
        assert await provider.quote(req) is result
        hit = await provider.quote(req)
        assert hit == result and hit.origem == "cache"
        assert result.origem == "api"
        inner.quote.assert_awaited_once_with(req)
        clock.now.return_value = now + timedelta(minutes=1)
        clock.today.return_value = clock.now.return_value.date()
        assert await provider.quote(req) is result
        assert inner.quote.await_count == 2
        await provider.quote(replace(req, cep="07000000"))
        assert inner.quote.await_count == 3
        assert conn.execute("SELECT count(*) FROM quote_cache").fetchone()[0] == 3
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "set"])
async def test_cache_errors_are_logged_without_pii(
    operation: str, caplog: pytest.LogCaptureFixture
) -> None:
    cache = AsyncMock(get=AsyncMock(return_value=None))
    getattr(cache, operation).side_effect = RuntimeError("CEP 01310100")
    result = Declined("Recusado")
    inner = AsyncMock(quote=AsyncMock(return_value=result))
    now = datetime(2026, 9, 11, tzinfo=UTC)
    provider = CachingQuoteProvider(inner, cache, Mock(now=Mock(return_value=now)))
    assert await provider.quote(QuoteRequest("completo", 30, 2026)) is result
    assert f"quote_cache_{operation}_failed" in caplog.text
    assert "01310100" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [QuoteUnavailable(), QuoteContractError(), asyncio.CancelledError()]
)
async def test_inner_failure_is_not_cached(error: BaseException) -> None:
    cache = AsyncMock(get=AsyncMock(return_value=None))
    inner = AsyncMock(quote=AsyncMock(side_effect=error))
    now = datetime(2026, 9, 11, tzinfo=UTC)
    provider = CachingQuoteProvider(inner, cache, Mock(now=Mock(return_value=now)))
    with pytest.raises(type(error)):
        await provider.quote(QuoteRequest("completo", 30, 2026))
    cache.set.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "set"])
async def test_cache_cancellation_propagates(operation: str) -> None:
    cache = AsyncMock(get=AsyncMock(return_value=None))
    getattr(cache, operation).side_effect = asyncio.CancelledError()
    now = datetime(2026, 9, 11, tzinfo=UTC)
    inner = AsyncMock(quote=AsyncMock(return_value=Declined("Recusado")))
    provider = CachingQuoteProvider(inner, cache, Mock(now=Mock(return_value=now)))
    with pytest.raises(asyncio.CancelledError):
        await provider.quote(QuoteRequest("completo", 30, 2026))
    if operation == "get":
        inner.quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_response_crossing_midnight_is_not_saved_for_next_day() -> None:
    now = datetime(2026, 9, 11, 23, 59, 59, tzinfo=UTC)
    clock = Mock(now=Mock(return_value=now))
    cache = AsyncMock(get=AsyncMock(return_value=None))

    async def respond(req: QuoteRequest) -> Declined:
        clock.now.return_value = now + timedelta(seconds=2)
        return Declined("Recusado")

    provider = CachingQuoteProvider(AsyncMock(quote=respond), cache, clock)
    await provider.quote(QuoteRequest("completo", 30, 2026))
    cache.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiration_is_next_local_midnight() -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=timezone(timedelta(hours=-3)))
    clock = Mock(now=Mock(return_value=now))
    cache = AsyncMock(get=AsyncMock(return_value=None))
    outcome = Declined("Recusado")
    provider = CachingQuoteProvider(AsyncMock(quote=AsyncMock(return_value=outcome)), cache, clock)
    req = QuoteRequest("completo", 30, 2026)
    await provider.quote(req)
    cache.set.assert_awaited_once_with(
        req.fingerprint(now.date()), outcome, datetime(2026, 9, 12, tzinfo=now.tzinfo)
    )
