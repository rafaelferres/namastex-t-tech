from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from itertools import count
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from application.tracing import Correlation
from domain.quote import Declined, Quote, QuoteContractError, QuoteRequest, QuoteUnavailable
from infrastructure.quote.hedge import HedgingQuoteProvider
from infrastructure.quote.http import HttpQuoteProvider
from infrastructure.quote.retry import RetryingQuoteProvider
from infrastructure.quote.trace import ApplicationTrace, WireTrace
from infrastructure.tracing.correlation import ContextCorrelationProvider
from tests.virtual_time import ScriptedProvider, Step, virtual_time


def correlation() -> ContextCorrelationProvider:
    return ContextCorrelationProvider(lambda: Correlation("trace-test", "conv-test"))


@pytest.mark.parametrize("status", [200, 422, 400, 503])
def test_wire_records_http_and_latency(status: int, quote_payload: dict[str, Any]) -> None:
    with virtual_time() as timeline:
        recorder = Mock(finish=AsyncMock())
        context = correlation()

        async def respond(req: httpx.Request) -> httpx.Response:
            await timeline.sleep(0.025)
            return httpx.Response(
                status,
                json=quote_payload
                if status == 200
                else {"motivo": "Recusado", "error": "upstream_unavailable"},
            )

        async def run() -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(respond), base_url="https://test"
            ) as client:
                leaf = HttpQuoteProvider(client, 2, timeline, observe_status=context.observe_http)
                wire = WireTrace(leaf, recorder, context, timeline)
                req = QuoteRequest("completo", 30, 2027)
                if status in (400, 503):
                    with pytest.raises(QuoteContractError if status == 400 else QuoteUnavailable):
                        await wire.quote(req)
                else:
                    result = await wire.quote(req)
                    assert isinstance(result, Quote if status == 200 else Declined)

        timeline.run(run())
        event = recorder.record.call_args.args[0]
        assert (
            event.status
            == {200: "quoted", 422: "declined", 400: "contract_error", 503: "unavailable"}[status]
        )
        assert event.http_status == status
        assert event.latencia_ms == 25
        assert event.ano_normalizado is True
        assert event.tentativa == 1 and not event.hedge
        assert event.trace_id == "trace-test"


def test_three_failures_record_each_wire_and_logical_resolution() -> None:
    with virtual_time() as timeline:
        recorder = Mock(finish=AsyncMock())
        ctx = correlation()
        leaf = ScriptedProvider(timeline, *(Step(QuoteUnavailable(), 0.01) for _ in range(3)))
        wire = WireTrace(leaf, recorder, ctx, timeline)
        retry = RetryingQuoteProvider(
            wire,
            max_attempts=3,
            base_delay=0.02,
            max_delay=0.02,
            budget=3.5,
            sleep=timeline.sleep,
            rng=lambda: 0.5,
            clock=timeline,
        )
        app = ApplicationTrace(retry, recorder, ctx, timeline)
        with pytest.raises(QuoteUnavailable):
            timeline.run(app.quote(QuoteRequest("completo", 30, 2026)))
        events = [c.args[0] for c in recorder.record.call_args_list]
        assert [e.tentativa for e in events] == [1, 2, 3, 0]
        assert all(e.status == "unavailable" for e in events)
        assert all(e.trace_id == "trace-test" for e in events)
        assert all(e.latencia_ms >= 9 for e in events)


def test_hedge_loser_is_recorded_with_cancellation() -> None:
    with virtual_time() as timeline:
        recorder = Mock(finish=AsyncMock())
        ctx = correlation()
        leaf = ScriptedProvider(timeline, Step(Declined("slow"), 8), Step(Declined("fast"), 0.01))
        wire = WireTrace(leaf, recorder, ctx, timeline)
        app = ApplicationTrace(
            HedgingQuoteProvider(wire, hedge_delay=0.1, sleep=timeline.sleep),
            recorder,
            ctx,
            timeline,
        )
        assert timeline.run(app.quote(QuoteRequest("completo", 30, 2026))).motivo == "fast"
        events = [c.args[0] for c in recorder.record.call_args_list]
        physical = sorted((e for e in events if e.tentativa), key=lambda e: e.tentativa)
        assert len(physical) == 2
        assert [e.hedge for e in physical] == [False, True]
        assert physical[0].erro == "CancelledError"
        assert physical[0].status == "unavailable"
        assert physical[0].http_status is None


@pytest.mark.parametrize("layer", [ApplicationTrace, WireTrace])
def test_record_failure_does_not_break_quote(layer: type, caplog: pytest.LogCaptureFixture) -> None:
    with virtual_time() as timeline:
        result = Declined("Recusado")
        recorder = Mock(finish=AsyncMock(), record=Mock(side_effect=RuntimeError("CEP 01310100")))
        provider = layer(
            ScriptedProvider(timeline, Step(result)), recorder, correlation(), timeline
        )
        assert timeline.run(provider.quote(QuoteRequest("completo", 30, 2026))) is result
        assert "trace_record_failed" in caplog.text
        assert "01310100" not in caplog.text


def test_cancelled_normalization_uses_api_calendar_not_utc_year() -> None:
    with virtual_time() as timeline:
        now = datetime(2026, 12, 31, 23, tzinfo=timezone(timedelta(hours=-3)))
        clock = Mock(
            now=Mock(return_value=now),
            today=Mock(return_value=date(2026, 12, 31)),
            monotonic=timeline.monotonic,
        )
        ctx = correlation()
        recorder = Mock(finish=AsyncMock())
        wire = WireTrace(
            ScriptedProvider(timeline, Step(Declined("slow"), 8), Step(Declined("fast"))),
            recorder,
            ctx,
            clock,
        )
        app = ApplicationTrace(
            HedgingQuoteProvider(wire, hedge_delay=0.1, sleep=timeline.sleep), recorder, ctx, clock
        )
        timeline.run(app.quote(QuoteRequest("completo", 30, 2027)))
        cancelled = next(
            c.args[0] for c in recorder.record.call_args_list if c.args[0].erro == "CancelledError"
        )
        assert cancelled.criado_em.year == 2027
        assert cancelled.ano_normalizado is True


def test_concurrent_quotes_keep_correlation_and_http_status_separate(
    quote_payload: dict[str, Any],
) -> None:
    with virtual_time() as timeline:
        sequence = count(1)
        ctx = ContextCorrelationProvider(lambda: Correlation(f"trace-{next(sequence)}", "conv"))
        recorder = Mock(finish=AsyncMock())

        async def respond(req: httpx.Request) -> httpx.Response:
            age = json.loads(req.content)["idade"]
            await timeline.sleep(0.02 if age == 30 else 0.01)
            return (
                httpx.Response(200, json=quote_payload)
                if age == 30
                else httpx.Response(422, json={"motivo": "Recusado"})
            )

        async def run() -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(respond), base_url="https://test"
            ) as client:
                wire = WireTrace(
                    HttpQuoteProvider(client, 2, timeline, observe_status=ctx.observe_http),
                    recorder,
                    ctx,
                    timeline,
                )
                app = ApplicationTrace(wire, recorder, ctx, timeline)
                await asyncio.gather(
                    *(app.quote(QuoteRequest("completo", age, 2026)) for age in (30, 31))
                )

        timeline.run(run())
        events = [c.args[0] for c in recorder.record.call_args_list]
        for trace_id, status in (("trace-1", 200), ("trace-2", 422)):
            pair = [e for e in events if e.trace_id == trace_id]
            assert [e.tentativa for e in pair] == [1, 0]
            assert pair[0].http_status == status and not pair[0].hedge
