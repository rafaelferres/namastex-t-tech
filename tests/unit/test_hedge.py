from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest

from domain.quote import Declined, Quote, QuoteContractError, QuoteRequest, QuoteUnavailable
from infrastructure.quote.hedge import HedgingQuoteProvider
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


def hedge(timeline: Timeline, leaf: ScriptedProvider) -> HedgingQuoteProvider:
    return HedgingQuoteProvider(leaf, hedge_delay=1.5, sleep=timeline.sleep)


@pytest.mark.parametrize("kind", ["quote", "declined", "unavailable", "contract"])
@pytest.mark.parametrize("latency", [0, 0.5, 1.5])
def test_first_completion_before_or_at_window_does_not_hedge(
    timeline: Timeline, quote_payload: dict[str, Any], kind: str, latency: float
) -> None:
    outcome = {
        "quote": Quote.from_api(quote_payload),
        "declined": Declined("Recusado"),
        "unavailable": QuoteUnavailable(),
        "contract": QuoteContractError(),
    }[kind]
    leaf = ScriptedProvider(timeline, Step(outcome, latency))
    if isinstance(outcome, Exception):
        with pytest.raises(type(outcome)) as caught:
            timeline.run(hedge(timeline, leaf).quote(REQ))
        assert caught.value is outcome
    else:
        assert timeline.run(hedge(timeline, leaf).quote(REQ)) is outcome
    assert leaf.requests == [REQ]
    assert leaf.finished == [0]
    assert timeline.monotonic() == latency


@pytest.mark.parametrize(
    ("first_latency", "second_latency", "winner"),
    [
        (4, 0, 1),
        (2, 1, 0),
        (4, 0.1, 1),
    ],
)
def test_first_success_wins_and_loser_is_cancelled(
    timeline: Timeline,
    quote_payload: dict[str, Any],
    first_latency: float,
    second_latency: float,
    winner: int,
) -> None:
    outcomes = [Quote.from_api(quote_payload), Quote.from_api(quote_payload)]
    leaf = ScriptedProvider(
        timeline, Step(outcomes[0], first_latency), Step(outcomes[1], second_latency)
    )
    assert timeline.run(hedge(timeline, leaf).quote(REQ)) is outcomes[winner]
    assert leaf.starts == [0, 1.5]
    assert leaf.requests == [REQ, REQ]
    assert leaf.cancelled == [1 - winner]
    assert sorted(leaf.finished) == [0, 1]


def test_decline_from_hedge_is_valid_response(timeline: Timeline) -> None:
    refusal = Declined("Não aceito")
    leaf = ScriptedProvider(timeline, Step(QuoteUnavailable(), 8), Step(refusal))
    assert timeline.run(hedge(timeline, leaf).quote(REQ)) is refusal
    assert leaf.cancelled == [0]


@pytest.mark.parametrize(("first_latency", "second_latency", "last"), [(2, 0.1, 0), (2, 1, 1)])
def test_both_unavailable_propagates_last_completed_error(
    timeline: Timeline, first_latency: float, second_latency: float, last: int
) -> None:
    errors = [QuoteUnavailable("primeira", suspeita_contrato=True), QuoteUnavailable("segunda")]
    leaf = ScriptedProvider(
        timeline, Step(errors[0], first_latency), Step(errors[1], second_latency)
    )
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(hedge(timeline, leaf).quote(REQ))
    assert caught.value is errors[last]
    assert caught.value.suspeita_contrato is (last == 0)
    assert caught.value.todas_falhas_suspeitas is False
    assert sorted(leaf.finished) == [0, 1]


def test_failure_after_hedge_started_waits_for_other_success(
    timeline: Timeline, quote_payload: dict[str, Any]
) -> None:
    quote = Quote.from_api(quote_payload)
    leaf = ScriptedProvider(timeline, Step(QuoteUnavailable(), 2), Step(quote, 1))
    assert timeline.run(hedge(timeline, leaf).quote(REQ)) is quote
    assert timeline.monotonic() == 2.5


@pytest.mark.parametrize("contract_call", [0, 1])
def test_contract_from_either_call_propagates_and_cancels_other(
    timeline: Timeline, quote_payload: dict[str, Any], contract_call: int
) -> None:
    error = QuoteContractError()
    quote = Quote.from_api(quote_payload)
    steps = (
        [Step(error, 2), Step(quote, 8)] if contract_call == 0 else [Step(quote, 8), Step(error)]
    )
    leaf = ScriptedProvider(timeline, *steps)
    with pytest.raises(QuoteContractError) as caught:
        timeline.run(hedge(timeline, leaf).quote(REQ))
    assert caught.value is error
    assert leaf.cancelled == [1 - contract_call]


@pytest.mark.parametrize("contract_call", [0, 1])
def test_contract_takes_priority_over_simultaneous_success(
    timeline: Timeline, quote_payload: dict[str, Any], contract_call: int
) -> None:
    error = QuoteContractError()
    quote = Quote.from_api(quote_payload)
    first, second = (error, quote) if contract_call == 0 else (quote, error)
    leaf = ScriptedProvider(timeline, Step(first, 2), Step(second, 0.5))
    with pytest.raises(QuoteContractError) as caught:
        timeline.run(hedge(timeline, leaf).quote(REQ))
    assert caught.value is error


@pytest.mark.parametrize("cancel_at", [0.5, 1.6])
def test_caller_cancellation_drains_all_children(timeline: Timeline, cancel_at: float) -> None:
    leaf = ScriptedProvider(timeline, Step(QuoteUnavailable(), 100), Step(QuoteUnavailable(), 100))

    async def scenario() -> None:
        task = asyncio.create_task(hedge(timeline, leaf).quote(REQ))
        await timeline.sleep(cancel_at)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert asyncio.all_tasks() == {asyncio.current_task()}

    timeline.run(scenario())
    expected = [0] if cancel_at < 1.5 else [0, 1]
    assert sorted(leaf.cancelled) == expected
    assert sorted(leaf.finished) == expected


def test_each_retry_attempt_is_hedged(timeline: Timeline, quote_payload: dict[str, Any]) -> None:
    quote = Quote.from_api(quote_payload)
    leaf = ScriptedProvider(
        timeline,
        Step(QuoteUnavailable(), 2),
        Step(QuoteUnavailable()),
        Step(QuoteUnavailable(), 2),
        Step(quote),
    )
    provider = RetryingQuoteProvider(
        hedge(timeline, leaf),
        max_attempts=3,
        base_delay=0.1,
        max_delay=1,
        budget=20,
        sleep=timeline.sleep,
        rng=lambda: 1,
        clock=timeline,
    )
    assert timeline.run(provider.quote(REQ)) is quote
    assert leaf.starts == pytest.approx([0, 1.5, 2.1, 3.6])
    assert leaf.cancelled == [2]
    assert leaf.requests == [REQ] * 4


def test_decline_in_failure_sequence_stops_retry(timeline: Timeline) -> None:
    refusal = Declined("Não aceito")
    leaf = ScriptedProvider(
        timeline, Step(QuoteUnavailable()), Step(QuoteUnavailable(), 2), Step(refusal)
    )
    provider = RetryingQuoteProvider(
        hedge(timeline, leaf),
        max_attempts=3,
        base_delay=0.1,
        max_delay=1,
        budget=20,
        sleep=timeline.sleep,
        rng=lambda: 1,
        clock=timeline,
    )
    assert timeline.run(provider.quote(REQ)) is refusal
    assert len(leaf.requests) == 3
    assert leaf.cancelled == [1]


def test_retry_deadline_cancels_both_hedged_calls(timeline: Timeline) -> None:
    leaf = ScriptedProvider(timeline, Step(QuoteUnavailable(), 100), Step(QuoteUnavailable(), 100))
    provider = RetryingQuoteProvider(
        hedge(timeline, leaf),
        max_attempts=3,
        base_delay=0.1,
        max_delay=1,
        budget=2,
        sleep=timeline.sleep,
        rng=lambda: 1,
        clock=timeline,
    )
    with pytest.raises(QuoteUnavailable) as caught:
        timeline.run(provider.quote(REQ))
    assert caught.value.tentativas == 1
    assert sorted(leaf.cancelled) == [0, 1]
    assert timeline.monotonic() == 2


@pytest.mark.parametrize("all_suspects", [False, True])
def test_contract_detection_preserves_evidence_from_both_physical_calls(
    timeline: Timeline, all_suspects: bool
) -> None:
    steps = []
    for _ in range(3):
        # The suspicious primary finishes last; the hedge may provide contrary evidence.
        steps.extend(
            [
                Step(QuoteUnavailable(suspeita_contrato=True), 2),
                Step(QuoteUnavailable(suspeita_contrato=all_suspects)),
            ]
        )
    leaf = ScriptedProvider(timeline, *steps)
    provider = RetryingQuoteProvider(
        hedge(timeline, leaf),
        max_attempts=3,
        base_delay=0.1,
        max_delay=1,
        budget=20,
        sleep=timeline.sleep,
        rng=lambda: 1,
        clock=timeline,
    )
    expected = QuoteContractError if all_suspects else QuoteUnavailable
    with pytest.raises(expected) as caught:
        timeline.run(provider.quote(REQ))
    assert caught.value.tentativas == 3
    assert len(leaf.requests) == 6


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf")])
def test_invalid_hedge_delay_is_rejected(timeline: Timeline, delay: float) -> None:
    with pytest.raises(ValueError):
        HedgingQuoteProvider(ScriptedProvider(timeline), hedge_delay=delay, sleep=timeline.sleep)
