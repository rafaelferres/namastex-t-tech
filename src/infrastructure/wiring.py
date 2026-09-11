from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

from application.ingest import IngestedTurn, Ingestor
from application.inspect_trace import InspectQuoteTrace
from application.ports import (
    AcceptanceRulesProvider,
    AttemptRecorder,
    Clock,
    QuoteCache,
    QuoteProvider,
)
from application.tracing import CorrelationProvider
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.privacy import PrivacyRedactor, install_redacting_logging
from infrastructure.quote.cache import CachingQuoteProvider
from infrastructure.quote.config import QuoteConfig
from infrastructure.quote.guard import EligibilityGuardProvider
from infrastructure.quote.hedge import HedgingQuoteProvider
from infrastructure.quote.http import HttpQuoteProvider
from infrastructure.quote.retry import RetryingQuoteProvider
from infrastructure.quote.trace import ApplicationTrace, WireTrace


def build_quote_provider(
    *,
    client: httpx.AsyncClient,
    cache: QuoteCache,
    rules: AcceptanceRulesProvider,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]],
    rng: Callable[[], float],
    recorder: AttemptRecorder,
    correlation: CorrelationProvider,
    config: QuoteConfig | None = None,
) -> QuoteProvider:
    config = config if config is not None else QuoteConfig()
    http = HttpQuoteProvider(
        client, timeout=config.timeout, clock=clock, observe_status=correlation.observe_http
    )
    wire = WireTrace(http, recorder, correlation, clock)
    hedge = HedgingQuoteProvider(wire, hedge_delay=config.hedge_delay, sleep=sleep)
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
    guard = EligibilityGuardProvider(cached, rules, clock)
    return ApplicationTrace(guard, recorder, correlation, clock)


def build_ingestor(
    connection: sqlite3.Connection,
    consume: Callable[[IngestedTurn], Awaitable[None]],
    *,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]],
    window: float = 0.5,
) -> Ingestor:
    privacy = PrivacyRedactor()
    install_redacting_logging(logging.getLogger(), privacy)
    store = SQLiteConversations(connection)
    return Ingestor(store, store, store, privacy, consume, clock=clock, sleep=sleep, window=window)


@contextmanager
def trace_inspector(path: Path) -> Iterator[InspectQuoteTrace]:
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False
    )
    try:
        yield InspectQuoteTrace(SQLiteAttempts(connection))
    finally:
        connection.close()
