from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from application.tracing import Correlation, WireObservation


@dataclass(slots=True)
class _Session:
    correlation: Correlation
    sequence: int = 0
    active: int = 0


class ContextCorrelationProvider:
    def __init__(self, factory: Callable[[], Correlation]) -> None:
        self._factory = factory
        self._session: ContextVar[_Session | None] = ContextVar("quote_session", default=None)
        self._wire: ContextVar[WireObservation | None] = ContextVar("quote_wire", default=None)

    @contextmanager
    def logical(self) -> Iterator[Correlation]:
        correlation = self._factory()
        token = self._session.set(_Session(correlation))
        try:
            yield correlation
        finally:
            self._session.reset(token)

    @contextmanager
    def physical(self) -> Iterator[WireObservation]:
        session = self._session.get()
        if session is None:
            with self.logical(), self.physical() as observation:
                yield observation
            return
        session.sequence += 1
        observation = WireObservation(session.correlation, session.sequence, session.active > 0)
        session.active += 1
        token = self._wire.set(observation)
        try:
            yield observation
        finally:
            session.active -= 1
            self._wire.reset(token)

    def observe_http(self, status: int) -> None:
        observation = self._wire.get()
        if observation is not None:
            observation.http_status = status
