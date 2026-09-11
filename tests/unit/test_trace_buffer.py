from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from application.tracing import QuoteAttempt
from domain.quote import Declined, QuoteRequest
from infrastructure.quote.hedge import HedgingQuoteProvider
from infrastructure.quote.retry import RetryingQuoteProvider
from infrastructure.quote.trace import ApplicationTrace, WireTrace
from infrastructure.tracing.recorder import BufferedAttemptRecorder
from tests.unit.test_trace import correlation
from tests.virtual_time import ScriptedProvider, Step, virtual_time


def test_slow_recording_cannot_turn_refusal_into_unavailability() -> None:
    with virtual_time() as timeline:
        events = []

        async def slow_record(event) -> None:
            await timeline.sleep(4)
            events.append(event)

        recorder = BufferedAttemptRecorder(slow_record)
        result = Declined("Recusado")
        leaf = ScriptedProvider(timeline, Step(result, 0.01))
        ctx = correlation()
        wire = WireTrace(leaf, recorder, ctx, timeline)
        hedge = HedgingQuoteProvider(wire, hedge_delay=0.1, sleep=timeline.sleep)
        retry = RetryingQuoteProvider(
            hedge,
            max_attempts=3,
            base_delay=0.02,
            max_delay=0.02,
            budget=3.5,
            sleep=timeline.sleep,
            rng=lambda: 0.5,
            clock=timeline,
        )
        app = ApplicationTrace(retry, recorder, ctx, timeline)

        async def run() -> None:
            assert await app.quote(QuoteRequest("completo", 30, 2026)) is result
            assert timeline.monotonic() == 8.01
            assert len(leaf.requests) == 1

        timeline.run(run())
        assert [event.status for event in events] == ["declined", "declined"]


def test_recording_failures_are_best_effort(caplog) -> None:
    with virtual_time() as timeline:
        recorder = BufferedAttemptRecorder(AsyncMock(side_effect=RuntimeError("CEP 01310100")))
        app = ApplicationTrace(
            ScriptedProvider(timeline, Step(Declined("Recusado"))),
            recorder,
            correlation(),
            timeline,
        )

        async def run() -> None:
            await app.quote(QuoteRequest("completo", 30, 2026))

        timeline.run(run())
        assert "trace_record_failed" in caplog.text
        assert "01310100" not in caplog.text


def event() -> QuoteAttempt:
    return QuoteAttempt(
        "trace",
        "conv",
        "hash",
        0,
        "quoted",
        "api",
        None,
        0,
        False,
        False,
        None,
        datetime(2026, 9, 11, tzinfo=UTC),
    )


def test_full_buffer_drops_and_logs_without_blocking(caplog) -> None:
    with virtual_time() as timeline:
        sink = AsyncMock()
        recorder = BufferedAttemptRecorder(sink, capacity=1)

        async def run() -> None:
            recorder.record(event())
            recorder.record(event())
            await recorder.finish("trace")

        timeline.run(run())
        sink.assert_awaited_once()
        assert "trace_buffer_full" in caplog.text


def test_cancelled_finish_drains_before_connection_owner_can_close() -> None:
    with virtual_time() as timeline:
        saved = []

        async def write(record) -> None:
            await timeline.sleep(4)
            saved.append(record)

        recorder = BufferedAttemptRecorder(write)

        async def run() -> None:
            recorder.record(event())
            flush = asyncio.create_task(recorder.finish("trace"))
            asyncio.get_running_loop().call_soon(flush.cancel)
            with pytest.raises(asyncio.CancelledError):
                await flush
            assert saved == [event()]

        timeline.run(run())


@pytest.mark.parametrize("cancel_caller", [False, True])
def test_worker_cancellation_settles_queued_deliveries(cancel_caller) -> None:
    with virtual_time() as timeline:
        started = asyncio.Event()
        saved = []
        workers = []

        async def write(record) -> None:
            if not workers:
                workers.append(asyncio.current_task())
                started.set()
                await asyncio.Event().wait()
            saved.append(record)

        recorder = BufferedAttemptRecorder(write)

        async def run() -> None:
            recorder.record(event())
            recorder.record(event())
            await started.wait()
            caller = asyncio.create_task(recorder.finish("trace"))
            await timeline.sleep(0.001)
            worker = workers[0]
            worker.cancel()
            if cancel_caller:
                caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
            assert saved == [event()]
            if cancel_caller:
                with pytest.raises(asyncio.CancelledError):
                    await caller
            else:
                await caller

        timeline.run(run())
