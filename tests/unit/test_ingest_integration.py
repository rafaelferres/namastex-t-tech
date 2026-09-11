from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest

from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.wiring import build_ingestor
from tests.unit.test_ingest import message


@pytest.mark.asyncio
async def test_ingestion_persists_only_redacted_body_and_hashed_identity():
    connection = connect(":memory:")
    store = SQLiteConversations(connection)
    turns = []
    elapsed = [0.0]
    clock = Mock(now=Mock(return_value=datetime(2026, 9, 11)), monotonic=lambda: elapsed[0])

    async def consume(turn):
        turns.append(turn)

    async def sleep(delay):
        elapsed[0] += delay

    try:
        async with build_ingestor(connection, consume, clock=clock, sleep=sleep) as ingest:
            incoming = message(body="CPF 529.982.247-25, cep 01310-100, está caro")
            assert await ingest.ingest(incoming)
            assert not await ingest.ingest(incoming)
        persisted = await store.messages("conv-a")
        assert persisted[0].corpo == "CPF [CPF], cep [CEP], está caro"
        lead = await store.lead("cli", "user-a")
        assert lead is not None and len(lead.cpf_hash) == 64
        assert lead.channel_user_id != "user-a"
        assert turns[0].objecoes_preco == 1
        assert (await store.get("conv-a")).objecoes_preco == 1
    finally:
        connection.close()
