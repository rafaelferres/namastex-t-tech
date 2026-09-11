from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable

from application.ports import QuoteProvider
from domain.quote import QuoteOutcome, QuoteRequest, QuoteUnavailable
from infrastructure.quote._tasks import cancel_and_wait


class HedgingQuoteProvider:
    def __init__(
        self,
        inner: QuoteProvider,
        *,
        hedge_delay: float,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        if not math.isfinite(hedge_delay) or hedge_delay < 0:
            raise ValueError("Janela de hedge deve ser finita e não negativa")
        self._inner = inner
        self._hedge_delay = hedge_delay
        self._sleep = sleep

    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        completed: list[int] = []

        async def invoke(index: int) -> QuoteOutcome:
            try:
                return await self._inner.quote(req)
            finally:
                completed.append(index)

        calls = [asyncio.create_task(invoke(0))]
        timer = asyncio.ensure_future(self._sleep(self._hedge_delay))
        try:
            await asyncio.wait((calls[0], timer), return_when=asyncio.FIRST_COMPLETED)
            if calls[0].done():
                return calls[0].result()
            timer.result()
            calls.append(asyncio.create_task(invoke(1)))
            pending = set(calls)
            last_error: QuoteUnavailable | None = None
            all_suspects = True
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                # Never hide a contract/programming error behind an available success.
                for task in done:
                    error = task.exception()
                    if error is not None and not isinstance(error, QuoteUnavailable):
                        raise error
                for index in completed:
                    task = calls[index]
                    if task in done:
                        try:
                            return task.result()
                        except QuoteUnavailable as error:
                            last_error = error
                            all_suspects = all_suspects and error.todas_falhas_suspeitas
            assert last_error is not None
            last_error.todas_falhas_suspeitas = all_suspects
            raise last_error
        finally:
            await cancel_and_wait(*calls, timer)
