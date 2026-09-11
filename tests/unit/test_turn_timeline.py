from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from application.inspect_trace import InspectQuoteTrace
from application.turns import TurnEvent
from domain.messages import InboundMessage
from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.persistence.turns import SQLiteTurnEvents


@pytest.mark.asyncio
async def test_turn_stages_are_durable_ordered_and_idempotent(tmp_path):
    connection = connect(tmp_path / "trace.sqlite")
    try:
        message = InboundMessage("replay", "c", "c", "text", "Olá", "m1", 0)
        await SQLiteConversations(connection).ensure(message, None, datetime(2026, 9, 11))
        store = SQLiteTurnEvents(connection)
        events = [
            TurnEvent("t", "c", stage, "ativa", latency, None, datetime(2026, 9, 11))
            for stage, latency in (("extract", 1580), ("policy", 1), ("converse", 750))
        ]
        for event in events:
            store.record(event)
            store.record(event)
        assert await store.read_turn("t") == tuple(events)
        from unittest.mock import AsyncMock

        inspector = InspectQuoteTrace(AsyncMock(read=AsyncMock(return_value=())), store)
        output = await inspector.execute("t")
        assert output.index("extract") < output.index("policy") < output.index("converse")
        assert "1580 ms" in output
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_decision_keeps_both_opinions_and_old_databases_gain_the_column(tmp_path):
    path = tmp_path / "trace.sqlite"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE turn_events (id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, "
        "conversation_id TEXT NOT NULL, etapa TEXT NOT NULL, status TEXT NOT NULL, "
        "latencia_ms INTEGER NOT NULL, erro TEXT, criado_em TEXT NOT NULL)"
    )
    legacy.commit()
    legacy.close()
    connection = connect(path)
    try:
        message = InboundMessage("replay", "c", "c", "text", "Olá", "m1", 0)
        await SQLiteConversations(connection).ensure(message, None, datetime(2026, 9, 11))
        store = SQLiteTurnEvents(connection)
        opinions = TurnEvent(
            "t", "c", "decisao", "segue", 0, None, datetime(2026, 9, 11),
            sugestao="pedido_de_humano",
        )
        store.record(opinions)
        assert await store.read_turn("t") == (opinions,)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_record_never_waits_for_sqlite_lock_on_the_event_loop(tmp_path):
    """Escrita síncrona travava o loop até o busy_timeout enquanto outra conexão escrevia."""
    path = tmp_path / "trace.sqlite"
    connection, blocker = connect(path), connect(path)
    try:
        message = InboundMessage("replay", "c", "c", "text", "Olá", "m1", 0)
        await SQLiteConversations(connection).ensure(message, None, datetime(2026, 9, 11))
        store = SQLiteTurnEvents(connection)
        blocker.execute("BEGIN IMMEDIATE")  # outra conexão segura o lock de escrita
        event = TurnEvent("t", "c", "extract", "ativa", 1480, None, datetime(2026, 9, 11))
        store.record(event)  # precisa voltar já, sem esperar o lock no event loop
        blocker.commit()
        assert await store.read_turn("t") == (event,)
    finally:
        blocker.close()
        connection.close()
