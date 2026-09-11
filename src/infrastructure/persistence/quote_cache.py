from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from threading import Lock

from application.ports import Clock
from domain.quote import Declined, Quote, QuoteOutcome


async def _run[T](operation: Callable[[], T]) -> T:
    """Cancelamento não pode abandonar um worker que ainda usa a conexão."""
    worker = asyncio.create_task(asyncio.to_thread(operation))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                raise
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


def _decimal_text(value: object) -> str:
    if not isinstance(value, Decimal):
        raise TypeError("Tipo não serializável no cache")
    return str(value)


def _encode(outcome: QuoteOutcome) -> str:
    return json.dumps(
        {"kind": "quoted" if isinstance(outcome, Quote) else "declined", "data": asdict(outcome)},
        default=_decimal_text,
    )


def _decode(serialized: str) -> QuoteOutcome:
    document = json.loads(serialized)
    data = document["data"]
    normalized = data["ano_normalizado"]
    origin = data["origem"]
    if not isinstance(normalized, bool) or origin not in ("api", "cache", "regra_local"):
        raise ValueError("Metadados de cache inválidos")
    outcome: QuoteOutcome
    if document["kind"] == "quoted":
        data["premio_mensal"] = Decimal(data["premio_mensal"])
        data["franquia"] = Decimal(data["franquia"])
        if data.get("primeiro_pagamento_pro_rata") is None:
            data.pop("primeiro_pagamento_pro_rata", None)
        else:
            first = data["primeiro_pagamento_pro_rata"]
            first["valor_primeiro_pagamento"] = Decimal(first["valor_primeiro_pagamento"])
        outcome = Quote.from_api(data)
    elif document["kind"] == "declined" and isinstance(data["motivo"], str):
        outcome = Declined(data["motivo"])
    else:
        raise ValueError("Resultado de cache inválido")
    return replace(outcome, ano_normalizado=normalized, origem=origin)


class SQLiteQuoteCache:
    """Conexão dedicada; operações serializadas fora do event loop."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock) -> None:
        self._connection = connection
        self._clock = clock
        self._lock = Lock()

    async def get(self, fingerprint: str) -> QuoteOutcome | None:
        row = await _run(partial(self._read, fingerprint))
        if row is None:
            return None
        serialized, expires_at = row
        if self._clock.now().astimezone(UTC) >= datetime.fromisoformat(expires_at):
            return None
        return _decode(serialized)

    def _read(self, fingerprint: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT outcome, expira_em FROM quote_cache WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            return (str(row[0]), str(row[1])) if row is not None else None

    async def set(self, fingerprint: str, outcome: QuoteOutcome, expires_at: datetime) -> None:
        await _run(
            partial(
                self._write, fingerprint, _encode(outcome), expires_at.astimezone(UTC).isoformat()
            )
        )

    def _write(self, fingerprint: str, serialized: str, expires_at: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO quote_cache (fingerprint, outcome, expira_em) VALUES (?, ?, ?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET outcome=excluded.outcome, "
                "expira_em=excluded.expira_em",
                (fingerprint, serialized, expires_at),
            )
