from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Literal

from application.ports import AttemptRecorder, Clock, QuoteProvider
from application.tracing import CorrelationProvider, QuoteAttempt
from domain.quote import Declined, QuoteOutcome, QuoteRequest, QuoteUnavailable

logger = logging.getLogger(__name__)


class _Trace:
    def __init__(
        self,
        inner: QuoteProvider,
        recorder: AttemptRecorder,
        correlation: CorrelationProvider,
        clock: Clock,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._correlation = correlation
        self._clock = clock

    def _record(self, event: QuoteAttempt) -> None:
        try:
            self._recorder.record(event)
        except Exception:
            logger.warning("trace_record_failed")


class WireTrace(_Trace):
    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        with self._correlation.physical() as wire:
            start = self._clock.monotonic()
            created = self._clock.now().astimezone(UTC)
            today = self._clock.today()
            fingerprint = req.fingerprint(today)
            outcome: QuoteOutcome | None = None
            error: BaseException | None = None
            try:
                outcome = await self._inner.quote(req)
                return outcome
            except BaseException as caught:
                error = caught
                raise
            finally:
                self._record(
                    QuoteAttempt(
                        wire.correlation.trace_id,
                        wire.correlation.conversation_id,
                        fingerprint,
                        wire.tentativa,
                        _status(outcome, error),
                        "api",
                        wire.http_status,
                        max(0, round((self._clock.monotonic() - start) * 1000)),
                        wire.hedge,
                        outcome.ano_normalizado
                        if outcome is not None
                        else getattr(error, "ano_normalizado", req.veiculo_ano == today.year + 1),
                        _error_name(error),
                        created,
                    )
                )


class ApplicationTrace(_Trace):
    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        with self._correlation.logical() as correlation:
            start = self._clock.monotonic()
            created = self._clock.now().astimezone(UTC)
            fingerprint = req.fingerprint(self._clock.today())
            outcome: QuoteOutcome | None = None
            error: BaseException | None = None
            try:
                outcome = await self._inner.quote(req)
                return outcome
            except BaseException as caught:
                error = caught
                raise
            finally:
                self._record(
                    QuoteAttempt(
                        correlation.trace_id,
                        correlation.conversation_id,
                        fingerprint,
                        0,
                        _status(outcome, error),
                        outcome.origem if outcome is not None else "api",
                        None,
                        max(0, round((self._clock.monotonic() - start) * 1000)),
                        False,
                        outcome.ano_normalizado
                        if outcome is not None
                        else getattr(error, "ano_normalizado", False),
                        _error_name(error),
                        created,
                    )
                )


def _status(
    outcome: QuoteOutcome | None, error: BaseException | None
) -> Literal["quoted", "declined", "unavailable", "contract_error"]:
    if error is not None:
        return (
            "unavailable"
            if isinstance(error, (QuoteUnavailable, asyncio.CancelledError))
            else "contract_error"
        )
    return "declined" if isinstance(outcome, Declined) else "quoted"


def _error_name(error: BaseException | None) -> str | None:
    if error is None:
        return None
    suffix = (
        "[suspeita_contrato]"
        if isinstance(error, QuoteUnavailable) and error.suspeita_contrato
        else ""
    )
    return type(error).__name__ + suffix
