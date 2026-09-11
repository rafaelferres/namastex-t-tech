"""Relógio/event loop virtual para Python 3.12; nunca bloqueia esperando tempo real."""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from domain.quote import QuoteOutcome, QuoteRequest


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self.elapsed = 0.0
        super().__init__()

    def time(self) -> float:
        return self.elapsed

    def _run_once(self) -> None:
        # CPython 3.12 seam: drain ready callbacks before advancing to the next timer.
        while self._scheduled and self._scheduled[0]._cancelled:
            heapq.heappop(self._scheduled)
            self._timer_cancelled_count -= 1
        if not self._ready:
            if not self._scheduled:
                raise AssertionError("Cenário bloqueado sem evento virtual agendado")
            self.elapsed = max(self.elapsed, self._scheduled[0]._when)
        super()._run_once()  # All timers due or callbacks ready: selector timeout is zero.


class Timeline:
    def __init__(self, runner: asyncio.Runner, loop: VirtualLoop) -> None:
        self.runner = runner
        self.loop = loop
        self.completed_sleeps: list[float] = []
        self.cancelled_sleeps: list[float] = []

    def today(self) -> date:
        return self.now().date()

    def now(self) -> datetime:
        return datetime(2026, 9, 11) + timedelta(seconds=self.monotonic())

    def monotonic(self) -> float:
        return self.loop.time()

    async def sleep(self, delay: float) -> None:
        future: asyncio.Future[None] = self.loop.create_future()
        handle = self.loop.call_at(self.monotonic() + delay, future.set_result, None)
        try:
            await future
            self.completed_sleeps.append(delay)
        except asyncio.CancelledError:
            self.cancelled_sleeps.append(delay)
            raise
        finally:
            handle.cancel()

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        return self.runner.run(coroutine)


@contextmanager
def virtual_time() -> Iterator[Timeline]:
    loop = VirtualLoop()
    with asyncio.Runner(loop_factory=lambda: loop) as runner:
        yield Timeline(runner, loop)


@dataclass(frozen=True)
class Step:
    outcome: QuoteOutcome | BaseException
    latency: float = 0.0


class ScriptedProvider:
    def __init__(self, timeline: Timeline, *steps: Step) -> None:
        self.timeline = timeline
        self.steps = steps
        self.requests: list[QuoteRequest] = []
        self.starts: list[float] = []
        self.cancelled: list[int] = []
        self.finished: list[int] = []

    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        index = len(self.requests)
        self.requests.append(req)
        self.starts.append(self.timeline.monotonic())
        step = self.steps[index]
        try:
            if step.latency:
                await self.timeline.sleep(step.latency)
            if isinstance(step.outcome, BaseException):
                raise step.outcome
            return step.outcome
        except asyncio.CancelledError:
            self.cancelled.append(index)
            raise
        finally:
            self.finished.append(index)
