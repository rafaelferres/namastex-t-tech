"""CEP durável em conversations.slots, purgado no encerramento; mensagem segue redigida."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.graph import TurnConfig
from agent.nodes.converse import ConversationResult
from agent.nodes.extract import ExtractionResult
from agent.schemas.slots import Slots
from domain.messages import InboundMessage, Intent
from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.wiring import SalesStack, open_sales_stack
from tests.fakes import FakeClock


def slots(age: int = 30) -> Slots:
    return Slots.model_validate(
        {
            key: {"valor": value, "status": "informado", "proveniencia": "digitado"}
            for key, value in {
                "idade": age,
                "veiculo_ano": 2020,
                "plano_id": "completo",
                "data_inicio": "2026-09-16",
            }.items()
        }
    )


@asynccontextmanager
async def stack_for(
    path: Path,
    plans_payload: dict[str, Any],
    quote_payload: dict[str, Any],
    quotes: list[dict[str, Any]],
    *,
    speech: ConversationResult,
    age: int = 30,
) -> AsyncIterator[SalesStack]:
    clock = FakeClock()

    async def sleep(delay: float) -> None:
        if delay >= 1:
            await asyncio.Future()
        clock.elapsed += delay
        await asyncio.sleep(0)

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/planos":
            return httpx.Response(200, json=plans_payload)
        quotes.append(json.loads(request.content))
        return httpx.Response(200, json=quote_payload)

    extractor = AsyncMock(extract=AsyncMock(return_value=ExtractionResult(slots(age))))
    converser = AsyncMock(converse=AsyncMock(return_value=speech))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(api), base_url="https://quote.test"
    ) as client:
        async with open_sales_stack(
            path,
            quote_client=client,
            extractor=extractor,
            converser=converser,
            clock=clock,
            sleep=sleep,
            rng=lambda: 0.5,
            turn_config=TurnConfig(),
        ) as stack:
            yield stack


async def say(stack: SalesStack, text: str, index: int) -> object:
    await stack.ingestor.ingest(
        InboundMessage("whatsapp", "conv-1", "5511999990000", "text", text, f"w{index}", index)
    )
    await stack.ingestor.wait_idle()
    return stack.session.latest_response("conv-1")


def stored(path: Path) -> tuple[tuple[str, str], int, list[str]]:
    connection = connect(path)
    try:
        row = connection.execute(
            "SELECT slots, status FROM conversations WHERE id='conv-1'"
        ).fetchone()
        checkpoints = connection.execute(
            "SELECT count(*) FROM checkpoints WHERE thread_id='conv-1'"
        ).fetchone()[0]
        bodies = [r[0] for r in connection.execute("SELECT corpo FROM messages").fetchall()]
    finally:
        connection.close()
    return (row[0], row[1]), checkpoints, bodies


@pytest.mark.asyncio
async def test_private_cep_survives_restart_and_reaches_the_quote(
    tmp_path, plans_payload, quote_payload
):
    path, quotes = tmp_path / "agent.sqlite", []
    ask = ConversationResult("Qual plano você prefere?", None)
    async with stack_for(path, plans_payload, quote_payload, quotes, speech=ask) as stack:
        assert (await say(stack, "CEP 01310-100", 0)).intent is Intent.CONVERSAR
    quote = ConversationResult("", None, "completo")
    async with stack_for(path, plans_payload, quote_payload, quotes, speech=quote) as stack:
        reply = await say(stack, "Pode cotar o completo", 1)
    assert reply.intent is Intent.APRESENTAR_COTACAO
    assert quotes[-1]["cep"] == "01310100"
    (slots_json, _), _, bodies = stored(path)
    assert json.loads(slots_json) == {"cep": "01310100"}
    assert all("01310" not in body for body in bodies)
    assert any("[CEP]" in body for body in bodies)


@pytest.mark.asyncio
async def test_closing_purges_private_slots_and_graph_state(
    tmp_path, plans_payload, quote_payload
):
    path, quotes = tmp_path / "agent.sqlite", []
    ask = ConversationResult("Qual plano você prefere?", None)
    async with stack_for(path, plans_payload, quote_payload, quotes, speech=ask) as stack:
        await say(stack, "CEP 01310-100", 0)
        await stack.session.close("conv-1")
    (slots_json, status), checkpoints, _ = stored(path)
    assert (json.loads(slots_json), status) == ({}, "encerrada")
    assert checkpoints == 0


@pytest.mark.asyncio
async def test_final_refusal_closes_and_purges(tmp_path, plans_payload, quote_payload):
    path, quotes = tmp_path / "agent.sqlite", []
    ask = ConversationResult("Qual plano você prefere?", None)
    async with stack_for(path, plans_payload, quote_payload, quotes, speech=ask, age=100) as stack:
        assert (await say(stack, "CEP 01310-100", 0)).intent is Intent.RECUSAR
    (slots_json, status), checkpoints, _ = stored(path)
    assert (json.loads(slots_json), status) == ({}, "encerrada")
    assert checkpoints == 0
    assert quotes == []


@pytest.mark.asyncio
async def test_first_collected_cep_is_immutable(tmp_path):
    connection = connect(tmp_path / "agent.sqlite")
    try:
        store = SQLiteConversations(connection)
        await store.ensure(
            InboundMessage("replay", "conv-1", "conv-1", "text", "", "seed", 0),
            None,
            datetime(2026, 9, 11),
        )
        await store.remember("conv-1", "01310100")
        await store.remember("conv-1", "99999999")
        assert await store.read("conv-1") == "01310100"
    finally:
        connection.close()
