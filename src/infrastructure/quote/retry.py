from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import NoReturn

from application.ports import Clock, QuoteProvider
from domain.quote import QuoteContractError, QuoteOutcome, QuoteRequest, QuoteUnavailable
from infrastructure.quote._tasks import cancel_and_wait


@dataclass(slots=True)
class _Progress:
    attempts: int = 0
    failures: int = 0
    all_suspects: bool = True
    last_error: QuoteUnavailable | None = None


class RetryingQuoteProvider:
    def __init__(
        self,
        inner: QuoteProvider,
        *,
        max_attempts: int,
        base_delay: float,
        max_delay: float,
        budget: float,
        sleep: Callable[[float], Awaitable[None]],
        rng: Callable[[], float],
        clock: Clock,
        contract_threshold: int = 3,
    ) -> None:
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("Número de tentativas deve ser inteiro positivo")
        if type(contract_threshold) is not int or contract_threshold < 2:
            raise ValueError("Limiar de contrato deve ser inteiro de pelo menos dois")
        if any(not math.isfinite(value) or value < 0 for value in (base_delay, max_delay, budget)):
            raise ValueError("Delays e orçamento devem ser finitos e não negativos")
        self._inner = inner
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._budget = budget
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        self._contract_threshold = contract_threshold

    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        progress = _Progress()
        if self._budget == 0:
            self._exhausted(progress)
        deadline = self._clock.monotonic() + self._budget
        worker = asyncio.create_task(self._run(req, progress, deadline))
        timer = asyncio.create_task(self._wait_deadline(deadline))
        try:
            await asyncio.wait((worker, timer), return_when=asyncio.FIRST_COMPLETED)
            if worker.done():
                return worker.result()
            timer.result()
            await cancel_and_wait(worker)
            self._exhausted(progress)
        finally:
            await cancel_and_wait(worker, timer)

    async def _wait_deadline(self, deadline: float) -> None:
        await self._sleep(max(0.0, deadline - self._clock.monotonic()))

    async def _run(self, req: QuoteRequest, progress: _Progress, deadline: float) -> QuoteOutcome:
        ceiling = min(self._base_delay, self._max_delay)
        while progress.attempts < self._max_attempts and self._clock.monotonic() < deadline:
            progress.attempts += 1
            try:
                return await self._inner.quote(req)
            except QuoteUnavailable as error:
                progress.failures += 1
                progress.last_error = error
                progress.all_suspects = progress.all_suspects and error.todas_falhas_suspeitas
            if progress.attempts == self._max_attempts:
                break
            jitter = self._rng()
            if not 0 <= jitter <= 1:
                raise ValueError("Jitter deve estar entre zero e um")
            delay = ceiling * jitter
            if delay >= deadline - self._clock.monotonic():
                break
            await self._sleep(delay)
            ceiling = min(self._max_delay, ceiling * 2)
        self._exhausted(progress)

    def _exhausted(self, progress: _Progress) -> NoReturn:
        suspects = progress.all_suspects and progress.failures == progress.attempts > 0
        normalized = progress.last_error.ano_normalizado if progress.last_error else False
        if suspects and progress.attempts >= self._contract_threshold:
            raise QuoteContractError(
                "Falhas repetidas com suspeita de contrato",
                ano_normalizado=normalized,
                tentativas=progress.attempts,
            ) from None
        raise QuoteUnavailable(
            "Orçamento de cotação esgotado",
            suspeita_contrato=suspects,
            ano_normalizado=normalized,
            tentativas=progress.attempts,
            detalhe=progress.last_error.detalhe if progress.last_error else None,
        ) from None
