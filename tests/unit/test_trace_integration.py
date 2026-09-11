from __future__ import annotations

import asyncio
from datetime import datetime
from itertools import count
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from application.inspect_trace import InspectQuoteTrace
from application.tracing import Correlation
from domain.acceptance import AcceptanceRules
from domain.quote import QuoteRequest
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.connection import connect
from infrastructure.persistence.quote_cache import SQLiteQuoteCache
from infrastructure.tracing.correlation import ContextCorrelationProvider
from infrastructure.tracing.recorder import BufferedAttemptRecorder
from infrastructure.wiring import build_quote_provider


@pytest.mark.asyncio
async def test_logical_origins_and_shared_correlation(
    plans_payload: dict[str, Any],
    quote_payload: dict[str, Any],
) -> None:
    rules = AcceptanceRules.from_api(plans_payload)
    age = min(f.minimo for f in rules.faixas_idade if f.motivo_recusa is None)
    rejected_age = max(f.maximo for f in rules.faixas_idade if f.motivo_recusa is None) + 1
    now = datetime(2026, 9, 11)
    clock = Mock(
        now=Mock(return_value=now),
        today=Mock(return_value=now.date()),
        monotonic=Mock(return_value=0),
    )
    cache_conn, trace_conn = connect(":memory:"), connect(":memory:")
    requests = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return httpx.Response(200, json=quote_payload)

    async def sleep(delay: float) -> None:
        await asyncio.Future()

    sequence = count(1)
    ctx = ContextCorrelationProvider(lambda: Correlation(f"trace-{next(sequence)}", "conv-1"))
    try:
        recorder = SQLiteAttempts(trace_conn)
        buffer = BufferedAttemptRecorder(recorder.record)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://test"
        ) as client:
            provider = build_quote_provider(
                client=client,
                cache=SQLiteQuoteCache(cache_conn, clock),
                rules=AsyncMock(current=AsyncMock(return_value=rules)),
                clock=clock,
                sleep=sleep,
                rng=lambda: 0.5,
                recorder=buffer,
                correlation=ctx,
            )
            for request_age in (rejected_age, age, age):
                await provider.quote(
                    QuoteRequest(sorted(rules.planos_validos)[0], request_age, 2026)
                )
        local = await recorder.read("trace-1")
        api = await recorder.read("trace-2")
        cache = await recorder.read("trace-3")
        assert [(e.tentativa, e.origem) for e in local] == [(0, "regra_local")]
        assert [(e.tentativa, e.origem) for e in api] == [(1, "api"), (0, "api")]
        assert [(e.tentativa, e.origem) for e in cache] == [(0, "cache")]
        assert api[0].trace_id == api[1].trace_id
        assert api[0].http_status == 200
        assert len(requests) == 1
        text = await InspectQuoteTrace(recorder).execute("trace-3")
        assert "Desfecho" in text and "cache" in text
        assert "Tentativa" not in text
    finally:
        cache_conn.close()
        trace_conn.close()


@pytest.mark.asyncio
async def test_inspection_orders_three_attempts() -> None:
    from application.tracing import QuoteAttempt

    now = datetime(2026, 9, 11)
    events = tuple(
        QuoteAttempt(
            "trace",
            "conv",
            "f",
            n,
            "unavailable",
            "api",
            503,
            20,
            False,
            False,
            "QuoteUnavailable",
            now,
        )
        for n in (1, 2, 3, 0)
    )
    reader = AsyncMock(read=AsyncMock(return_value=events))
    text = await InspectQuoteTrace(reader).execute("trace")
    assert (
        text.index("Tentativa 1")
        < text.index("Tentativa 2")
        < text.index("Tentativa 3")
        < text.index("Desfecho")
    )
    assert "503" in text and "20 ms" in text and "QuoteUnavailable" in text
