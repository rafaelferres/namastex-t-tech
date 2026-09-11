"""Cotação real de perfil sintético, seguida de hit de cache, fora da suíte offline."""

from __future__ import annotations

import argparse
import asyncio
import random
from pathlib import Path
from uuid import uuid4

import httpx

from application.ports import SystemClock
from application.tracing import Correlation
from domain.quote import QuoteRequest
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.connection import connect
from infrastructure.persistence.quote_cache import SQLiteQuoteCache
from infrastructure.planos.client import PlanosClient
from infrastructure.tracing.correlation import ContextCorrelationProvider
from infrastructure.tracing.recorder import BufferedAttemptRecorder
from infrastructure.wiring import build_quote_provider


async def demo(url: str, database: Path) -> None:
    clock = SystemClock()
    cache_conn, trace_conn = connect(database), connect(database)
    conversation = uuid4().hex
    correlation = ContextCorrelationProvider(lambda: Correlation(uuid4().hex, conversation))
    store = SQLiteAttempts(trace_conn)
    recorder = BufferedAttemptRecorder(store.record)
    try:
        async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
            provider = build_quote_provider(
                client=client,
                cache=SQLiteQuoteCache(cache_conn, clock),
                rules=PlanosClient(client, clock=clock, ttl=300, timeout=2),
                clock=clock,
                sleep=asyncio.sleep,
                rng=random.Random(42).random,
                recorder=recorder,
                correlation=correlation,
            )
            request = QuoteRequest("completo", 30, clock.today().year)
            for _ in range(2):
                await provider.quote(request)
        rows = trace_conn.execute(
            "SELECT trace_id FROM quote_attempts WHERE conversation_id=? AND tentativa=0 "
            "ORDER BY rowid",
            (conversation,),
        ).fetchall()
        for row in rows:
            print(row[0])
    finally:
        cache_conn.close()
        trace_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    parser.add_argument("--database", type=Path, default=Path("autoseguro.sqlite"))
    args = parser.parse_args()
    asyncio.run(demo(args.url, args.database))


if __name__ == "__main__":
    main()
