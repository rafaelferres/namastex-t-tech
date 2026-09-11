from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterator
from typing import Any

import pytest

from domain.quote import Declined, Quote, QuoteContractError, QuoteRequest, QuoteUnavailable
from infrastructure.quote.retry import RetryingQuoteProvider
from tests.virtual_time import ScriptedProvider, Step, Timeline, virtual_time

REQ = QuoteRequest("completo", 30, 2026, "01310100")


@pytest.fixture
def timeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[Timeline]:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Sleep real não é permitido")

    monkeypatch.setattr(asyncio, "sleep", forbidden)
    monkeypatch.setattr(time, "sleep", forbidden)
    with virtual_time() as clock:
        yield clock


def retry(timeline: Timeline, leaf: ScriptedProvider, **overrides: Any) -> RetryingQuoteProvider:
    config = dict(
        max_attempts=3,
        base_delay=1.0,
        max_delay=4.0,
        budget=30.0,
        sleep=timeline.sleep,
        rng=lambda: 1.0,
        clock=timeline,
    )
    config.update(overrides)
    return RetryingQuoteProvider(leaf, **config)


@pytest.mark.parametrize("kind", ["quote", "declined", "contract", "unexpected"])
def test_only_unavailable_is_retried(
    timeline: Timeline, quote_payload: dict[str, Any], kind: str
) -> None:
    value = {
        "quote": Quote.from_api(quote_payload),
        "declined": Declined("Recusado"),
        "contract": QuoteContractError(),
        "unexpected": ValueError("bug"),
    }[kind]
    leaf = ScriptedProvider(timeline, Step(value))
    if isinstance(value, Exception):
        with pytest.raises(type(value)) as caught:
            timeline.run(retry(timeline, leaf).quote(REQ))
        assert caught.value is value
    else:
        assert timeline.run(retry(timeline, leaf).quote(REQ)) is value
    assert leaf.requests == [REQ]
    assert timeline.completed_sleeps == []


def test_two_failures_then_success(timeline: Timeline, quote_payload: dict[str, Any]) -> None:
    quote = Quote.from_api(quote_payload)
    leaf = ScriptedProvider(
        timeline, Step(QuoteUnavailable()), Step(QuoteUnavailable()), Step(quote)
    )
    assert timeline.run(retry(timeline, leaf).quote(REQ)) is quote
    assert leaf.requests == [REQ] * 3
    assert leaf.starts == [0, 1, 3]
    assert timeline.completed_sleeps == [1, 2]


def test_exhaustion_reports_attempts_and_metadata(timeline: Timeline) -> None:
    leaf = ScriptedProvider(timeline, *[Step(QuoteUnavailable(ano_normalizado=True))] * 3)
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(retry(timeline, leaf).quote(REQ))
    assert caught.value.tentativas == 3
    assert caught.value.ano_normalizado is True
    assert len(leaf.requests) == 3


@pytest.mark.parametrize("budget", [0.5, 1.0])
def test_delay_that_consumes_remaining_budget_is_not_started(
    timeline: Timeline, budget: float
) -> None:
    leaf = ScriptedProvider(timeline, Step(QuoteUnavailable()))
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(retry(timeline, leaf, budget=budget).quote(REQ))
    assert caught.value.tentativas == 1
    assert timeline.completed_sleeps == []
    assert timeline.monotonic() == 0


def test_budget_includes_request_latency(timeline: Timeline) -> None:
    leaf = ScriptedProvider(timeline, Step(QuoteUnavailable(), 2))
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(retry(timeline, leaf, budget=2.5).quote(REQ))
    assert caught.value.tentativas == 1
    assert timeline.monotonic() == 2
    assert timeline.completed_sleeps == [2]


def test_budget_cancels_in_flight_call(timeline: Timeline) -> None:
    leaf = ScriptedProvider(timeline, Step(Declined("Resposta tardia"), 100))
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(retry(timeline, leaf, budget=2.5).quote(REQ))
    assert caught.value.tentativas == 1
    assert timeline.monotonic() == 2.5
    assert leaf.cancelled == [0]
    assert leaf.finished == [0]


def test_deadline_is_measured_from_entry_not_timer_task_start(timeline: Timeline) -> None:
    class StartupWork(ScriptedProvider):
        async def quote(self, req: QuoteRequest) -> Quote | Declined:
            # Simulates synchronous work before the HTTP await, without busy waiting.
            timeline.loop.elapsed += 1
            return await super().quote(req)

    leaf = StartupWork(timeline, Step(QuoteUnavailable(), 100))
    with pytest.raises(QuoteUnavailable):
        timeline.run(retry(timeline, leaf, budget=2.5).quote(REQ))
    assert timeline.monotonic() == 2.5
    assert leaf.cancelled == [0]


@pytest.mark.parametrize("latency", [0, 100])
def test_caller_cancellation_stops_backoff_or_call_and_drains_tasks(
    timeline: Timeline, latency: float
) -> None:
    leaf = ScriptedProvider(timeline, Step(QuoteUnavailable(), latency))

    async def scenario() -> None:
        task = asyncio.create_task(retry(timeline, leaf).quote(REQ))
        await timeline.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert asyncio.all_tasks() == {asyncio.current_task()}

    timeline.run(scenario())
    assert leaf.requests == [REQ]
    assert leaf.cancelled == ([0] if latency else [])


def test_zero_budget_makes_no_attempt(timeline: Timeline) -> None:
    leaf = ScriptedProvider(timeline)
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(retry(timeline, leaf, budget=0).quote(REQ))
    assert caught.value.tentativas == 0
    assert leaf.requests == []


def test_backoff_grows_and_stays_capped(timeline: Timeline) -> None:
    leaf = ScriptedProvider(timeline, *[Step(QuoteUnavailable())] * 6)
    with pytest.raises(QuoteUnavailable):
        timeline.run(retry(timeline, leaf, max_attempts=6, max_delay=2.5).quote(REQ))
    assert timeline.completed_sleeps == [1, 2, 2.5, 2.5, 2.5]


def test_seeded_full_jitter_is_reproducible() -> None:
    observations = []
    for _ in range(2):
        with virtual_time() as clock:
            leaf = ScriptedProvider(clock, *[Step(QuoteUnavailable())] * 4)
            with pytest.raises(QuoteUnavailable):
                clock.run(
                    retry(clock, leaf, max_attempts=4, rng=random.Random(42).random).quote(REQ)
                )
            observations.append(clock.completed_sleeps)
    assert observations[0] == observations[1]
    assert observations[0] == pytest.approx([0.6394267985, 0.0500215104, 1.1001172735])


@pytest.mark.parametrize(
    ("suspects", "threshold", "contract"),
    [
        ([True, True, True], 3, True),
        ([True, False, True], 3, False),
        ([True, True], 3, False),
        ([True, True], 2, True),
    ],
)
def test_contract_promotion_requires_all_failures_and_threshold(
    timeline: Timeline, suspects: list[bool], threshold: int, contract: bool
) -> None:
    leaf = ScriptedProvider(
        timeline, *[Step(QuoteUnavailable(suspeita_contrato=s)) for s in suspects]
    )
    error = QuoteContractError if contract else QuoteUnavailable
    with pytest.raises(error) as caught:
        timeline.run(
            retry(timeline, leaf, max_attempts=len(suspects), contract_threshold=threshold).quote(
                REQ
            )
        )
    assert caught.value.tentativas == len(suspects)
    assert len(leaf.requests) == len(suspects)


def test_suspects_before_success_do_not_trigger_early_promotion(
    timeline: Timeline, quote_payload: dict[str, Any]
) -> None:
    quote = Quote.from_api(quote_payload)
    leaf = ScriptedProvider(
        timeline, *[Step(QuoteUnavailable(suspeita_contrato=True))] * 3, Step(quote)
    )
    assert timeline.run(retry(timeline, leaf, max_attempts=4).quote(REQ)) is quote


def test_budget_exhaustion_below_threshold_does_not_promote(timeline: Timeline) -> None:
    leaf = ScriptedProvider(timeline, *[Step(QuoteUnavailable(suspeita_contrato=True))] * 2)
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(retry(timeline, leaf, budget=2).quote(REQ))
    assert caught.value.tentativas == 2


@pytest.mark.parametrize(
    "config",
    [
        {"max_attempts": 0},
        {"max_attempts": True},
        {"base_delay": -1},
        {"max_delay": float("inf")},
        {"budget": float("nan")},
        {"contract_threshold": 1},
    ],
)
def test_invalid_configuration_is_rejected(timeline: Timeline, config: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        retry(timeline, ScriptedProvider(timeline), **config)
