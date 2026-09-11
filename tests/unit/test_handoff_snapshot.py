"""Snapshot de escalação montado pela composição real, sem correlação de teste."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.graph import TurnConfig
from agent.nodes.converse import ConversationResult
from agent.nodes.extract import ExtractionResult
from agent.schemas.slots import Slots
from domain.handoff import CollectedSlot, HandoffReason
from domain.messages import InboundMessage, Intent
from infrastructure.persistence.connection import connect
from infrastructure.persistence.delivery import SQLiteDelivery
from infrastructure.wiring import open_sales_stack
from tests.fakes import FakeClock


def slots() -> Slots:
    return Slots.model_validate(
        {
            key: {"valor": value, "status": "informado", "proveniencia": "digitado"}
            for key, value in {
                "idade": 30,
                "veiculo_ano": 2020,
                "plano_id": "completo",
                "data_inicio": "2026-09-16",
            }.items()
        }
    )


@pytest.mark.asyncio
async def test_exhausted_quote_escalates_with_attempts_of_the_conversation(
    tmp_path, plans_payload
):
    path = tmp_path / "agent.sqlite"
    clock = FakeClock()

    async def sleep(delay: float) -> None:
        # Jitter, hedge e janela avançam o relógio; o timer de orçamento (3,5 s) não vence.
        if delay >= 1:
            await asyncio.Future()
        clock.elapsed += delay
        await asyncio.sleep(0)

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/planos":
            return httpx.Response(200, json=plans_payload)
        return httpx.Response(503, json={"error": "upstream_unavailable"})

    extractor = AsyncMock(extract=AsyncMock(return_value=ExtractionResult(slots())))
    converser = AsyncMock(converse=AsyncMock(return_value=ConversationResult("", None, "completo")))
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
            await stack.ingestor.ingest(
                InboundMessage(
                    "whatsapp", "conv-1", "5511999990000", "text", "Quero o completo", "w1", 0
                )
            )
            await stack.ingestor.wait_idle()
            reply = stack.session.latest_response("conv-1")

    connection = connect(path)
    try:
        (identifier,) = connection.execute(
            "SELECT id FROM handoffs WHERE conversation_id=?", ("conv-1",)
        ).fetchone()
        decision = await SQLiteDelivery(connection).handoff(identifier)
        rows = connection.execute(
            "SELECT trace_id, tentativa, status, origem, http_status, latencia_ms "
            "FROM quote_attempts WHERE conversation_id=?",
            ("conv-1",),
        ).fetchall()
    finally:
        connection.close()

    assert reply is not None and reply.intent is Intent.ESCALAR
    assert decision is not None and decision.motivo is HandoffReason.COTACAO
    snapshot = decision.snapshot
    assert snapshot is not None
    attempts = {
        (a.trace_id, a.tentativa, a.status, a.origem, a.http_status, a.latencia_ms)
        for a in snapshot.tentativas
    }
    assert attempts == set(rows)
    assert {item.trace_id for item in snapshot.tentativas} == {identifier}
    physical = [item for item in snapshot.tentativas if item.tentativa > 0]
    assert len(physical) >= 3
    assert all(item.status == "unavailable" and item.http_status == 503 for item in physical)
    assert snapshot.slots["idade"] == CollectedSlot(30, "digitado")
    assert snapshot.slots["veiculo_ano"] == CollectedSlot(2020, "digitado")
