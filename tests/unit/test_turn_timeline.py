from __future__ import annotations

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
