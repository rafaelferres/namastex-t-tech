from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from application.tracing import Correlation
from domain.acceptance import AcceptanceRules
from domain.quote import Declined, Quote, QuoteRequest, QuoteUnavailable
from infrastructure.persistence.connection import connect
from infrastructure.persistence.quote_cache import SQLiteQuoteCache
from infrastructure.quote.config import QuoteConfig
from infrastructure.tracing.correlation import ContextCorrelationProvider
from infrastructure.wiring import build_quote_provider
from tests.virtual_time import virtual_time


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["ineligible", "quoted", "unavailable", "future", "declined"])
async def test_complete_chain(
    plans_payload: dict[str, Any],
    quote_payload: dict[str, Any],
    scenario: str,
) -> None:
    rules = AcceptanceRules.from_api(plans_payload)
    age = min(f.minimo for f in rules.faixas_idade if f.motivo_recusa is None)
    if scenario == "ineligible":
        age = max(f.maximo for f in rules.faixas_idade if f.motivo_recusa is None) + 1
    now = datetime(2026, 9, 11)
    clock = Mock(
        now=Mock(return_value=now),
        today=Mock(return_value=now.date()),
        monotonic=Mock(return_value=0.0),
    )
    requests: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(json.loads(req.content))
        assert req.extensions["timeout"]["read"] == 2.0
        if scenario == "unavailable":
            return httpx.Response(503, json={"error": "upstream_unavailable"})
        if scenario == "declined":
            return httpx.Response(422, json={"motivo": "Recusado pela API"})
        return httpx.Response(200, json=quote_payload)

    async def sleep(delay: float) -> None:
        if delay >= 1:
            await asyncio.Future()  # Deadline/hedge are cancelled by immediate HTTP completion.

    conn = connect(":memory:")
    try:
        cache = SQLiteQuoteCache(conn, clock)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://test"
        ) as client:
            provider = build_quote_provider(
                recorder=Mock(),
                correlation=ContextCorrelationProvider(lambda: Correlation("test", "conv")),
                client=client,
                cache=cache,
                rules=AsyncMock(current=AsyncMock(return_value=rules)),
                clock=clock,
                sleep=sleep,
                rng=lambda: 0.5,
            )
            req = QuoteRequest(
                sorted(rules.planos_validos)[0],
                age,
                2027 if scenario == "future" else 2026,
                "01310100",
            )
            if scenario == "unavailable":
                with pytest.raises(QuoteUnavailable) as raised:
                    await provider.quote(req)
                assert raised.value.tentativas == 3
                assert len(requests) == 3
            elif scenario == "ineligible":
                result = await provider.quote(req)
                assert isinstance(result, Declined) and result.origem == "regra_local"
                assert requests == []
            else:
                first = await provider.quote(req)
                second = await provider.quote(req)
                assert isinstance(first, Declined if scenario == "declined" else Quote)
                assert first.origem == "api" and second.origem == "cache"
                assert second == first
                assert len(requests) == 1
                assert requests[0]["cep"] == "01310100"
                assert requests[0]["veiculo_ano"] == 2026
                assert first.ano_normalizado == (scenario == "future")
                assert second.ano_normalizado == first.ano_normalizado
        count = conn.execute("SELECT count(*) FROM quote_cache").fetchone()[0]
        assert count == (0 if scenario in ("ineligible", "unavailable") else 1)
    finally:
        conn.close()


def test_quote_budget_is_configurable() -> None:
    assert QuoteConfig().budget == 3.5
    assert QuoteConfig(budget=2.5).budget == 2.5


@pytest.mark.parametrize("budget", [2.5, 3.5])
def test_wiring_enforces_configured_deadline(budget: float) -> None:
    with virtual_time() as timeline:
        cancelled: list[bool] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            try:
                await timeline.sleep(8)
                return httpx.Response(503)
            finally:
                cancelled.append(True)

        async def run() -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="https://test"
            ) as client:
                cache = AsyncMock(get=AsyncMock(return_value=None))
                provider = build_quote_provider(
                    recorder=Mock(),
                    correlation=ContextCorrelationProvider(lambda: Correlation("test", "conv")),
                    client=client,
                    cache=cache,
                    rules=AsyncMock(current=AsyncMock(return_value=None)),
                    clock=timeline,
                    sleep=timeline.sleep,
                    rng=lambda: 0.5,
                    config=QuoteConfig(budget=budget),
                )
                with pytest.raises(QuoteUnavailable):
                    await provider.quote(QuoteRequest("completo", 30, 2026))
                cache.set.assert_not_awaited()
                assert asyncio.all_tasks() == {asyncio.current_task()}

        timeline.run(run())
        assert timeline.monotonic() == budget
        assert len(cancelled) == 2
