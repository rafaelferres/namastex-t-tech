from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import pytest

from application.ports import QuoteProvider
from domain.quote import Quote, QuoteOutcome, QuoteRequest, QuoteUnavailable
from infrastructure.quote.config import PRODUCTION_QUOTE_BUDGET, QuoteConfig
from infrastructure.quote.hedge import HedgingQuoteProvider
from infrastructure.quote.retry import RetryingQuoteProvider
from tests.virtual_time import Timeline, virtual_time

TRIALS = 10_000
SEED = 42
CONFIG = QuoteConfig()


class ProbabilisticApi:
    def __init__(self, timeline: Timeline, quote: Quote) -> None:
        self.timeline = timeline
        self.result = quote
        self.rng = random.Random(SEED)
        self.calls = 0

    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        self.calls += 1
        roll = self.rng.random()
        if roll < 0.20:
            raise QuoteUnavailable()
        if roll < 0.30:
            # An eight-second API response hits the HTTP client's two-second timeout.
            await self.timeline.sleep(CONFIG.timeout)
            raise QuoteUnavailable()
        return self.result


@pytest.mark.parametrize(
    ("use_hedge", "budget", "expected"),
    [
        (False, 20.0, 0.027),
        (True, 20.0, 0.23**3),
        (False, PRODUCTION_QUOTE_BUDGET, 0.0329),
        (True, PRODUCTION_QUOTE_BUDGET, 0.0243),
    ],
)
@pytest.mark.parametrize("calibration", ["previous", "current"])
def test_seeded_residual_failure_rate(
    quote_payload: dict[str, Any],
    use_hedge: bool,
    expected: float,
    budget: float,
    calibration: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("A simulação não pode dormir em tempo real")

    monkeypatch.setattr(asyncio, "sleep", forbidden)
    monkeypatch.setattr(time, "sleep", forbidden)
    config = (
        QuoteConfig(hedge_delay=1.5, base_delay=0.1, max_delay=0.4)
        if calibration == "previous"
        else CONFIG
    )
    if calibration == "current" and use_hedge and budget == PRODUCTION_QUOTE_BUDGET:
        expected = 0.0127
    with virtual_time() as timeline:
        leaf = ProbabilisticApi(timeline, Quote.from_api(quote_payload))
        inner: QuoteProvider = leaf
        if use_hedge:
            inner = HedgingQuoteProvider(
                inner, hedge_delay=config.hedge_delay, sleep=timeline.sleep
            )
        chain = RetryingQuoteProvider(
            inner,
            max_attempts=config.max_attempts,
            base_delay=config.base_delay,
            max_delay=config.max_delay,
            budget=budget,
            sleep=timeline.sleep,
            rng=random.Random(2026).random,
            clock=timeline,
        )
        req = QuoteRequest("completo", 30, 2026, "01310100")

        async def trials() -> int:
            failures = 0
            for _ in range(TRIALS):
                try:
                    assert await chain.quote(req) is leaf.result
                except QuoteUnavailable as error:
                    assert 1 <= error.tentativas <= 3
                    failures += 1
            assert asyncio.all_tasks() == {asyncio.current_task()}
            return failures

        failures = timeline.run(trials())
    rate = failures / TRIALS
    print(
        f"{calibration}, hedge={use_hedge}, budget={budget}: {failures}/{TRIALS} = {rate:.4%}; "
        f"physical_calls={leaf.calls}"
    )
    # Unbounded: theoretical reference. Production: measured regression baseline (D-008).
    # 0.5 percentage points; fixed seed and schedule avoid stochastic CI failures.
    assert abs(rate - expected) < 0.005
