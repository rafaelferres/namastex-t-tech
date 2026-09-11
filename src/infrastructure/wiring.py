from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from application.ports import AcceptanceRulesProvider, Clock, QuoteCache, QuoteProvider
from infrastructure.quote.cache import CachingQuoteProvider
from infrastructure.quote.config import QuoteConfig
from infrastructure.quote.guard import EligibilityGuardProvider
from infrastructure.quote.hedge import HedgingQuoteProvider
from infrastructure.quote.http import HttpQuoteProvider
from infrastructure.quote.retry import RetryingQuoteProvider


def build_quote_provider(
    *,
    client: httpx.AsyncClient,
    cache: QuoteCache,
    rules: AcceptanceRulesProvider,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]],
    rng: Callable[[], float],
    config: QuoteConfig | None = None,
) -> QuoteProvider:
    config = config if config is not None else QuoteConfig()
    http = HttpQuoteProvider(client, timeout=config.timeout, clock=clock)
    hedge = HedgingQuoteProvider(http, hedge_delay=config.hedge_delay, sleep=sleep)
    retry = RetryingQuoteProvider(
        hedge,
        max_attempts=config.max_attempts,
        base_delay=config.base_delay,
        max_delay=config.max_delay,
        budget=config.budget,
        sleep=sleep,
        rng=rng,
        clock=clock,
        contract_threshold=config.contract_threshold,
    )
    cached = CachingQuoteProvider(retry, cache, clock)
    return EligibilityGuardProvider(cached, rules, clock)
