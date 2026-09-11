from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, time, timedelta

from application.ports import Clock, QuoteCache, QuoteProvider
from domain.quote import QuoteOutcome, QuoteRequest

logger = logging.getLogger(__name__)


class CachingQuoteProvider:
    def __init__(self, inner: QuoteProvider, cache: QuoteCache, clock: Clock) -> None:
        self._inner = inner
        self._cache = cache
        self._clock = clock

    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        now = self._clock.now()
        key = req.fingerprint(now.date())
        expires_at = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)
        try:
            cached = await self._cache.get(key)
        except Exception:
            logger.warning("quote_cache_get_failed")
            cached = None
        if cached is not None and self._clock.now() < expires_at:
            return replace(cached, origem="cache")
        outcome = await self._inner.quote(req)
        if self._clock.now() < expires_at:
            try:
                await self._cache.set(key, outcome, expires_at)
            except Exception:
                logger.warning("quote_cache_set_failed")
        return outcome
