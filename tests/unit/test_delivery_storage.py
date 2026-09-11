from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from application.tracing import QuoteAttempt
from domain.handoff import (
    CollectedSlot,
    ConversationContext,
    HandoffDecision,
    HandoffPolicy,
    HandoffReason,
    HandoffSnapshot,
    HandoffSuggestion,
)
from domain.messages import ApresentarCotacao, Intent, OutboundMessage, PedirDado
from domain.product import ProductFacts
from domain.quote import Declined, Quote
from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from tests.unit.test_conversation_storage import message


@pytest.mark.asyncio
async def test_durable_intents_money_and_handoff(quote_payload: dict[str, Any]) -> None:
    from infrastructure.persistence.delivery import SQLiteDelivery

    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, tzinfo=UTC)
        await SQLiteConversations(conn).ensure(message(), None, now)
        store = SQLiteDelivery(conn)
        quote = Quote.from_api(quote_payload)
        decision = HandoffPolicy().evaluate(
            ConversationContext(
                pede_humano=True,
                slots={
                    "data_inicio": CollectedSlot(date(2026, 9, 15), "digitado"),
                    "cep": CollectedSlot("01310100", "digitado"),
                },
                tentativas=(
                    QuoteAttempt(
                        "trace",
                        "conv-a",
                        "a" * 64,
                        1,
                        "quoted",
                        "api",
                        200,
                        5,
                        False,
                        False,
                        None,
                        now,
                    ),
                ),
                sugestao_llm=HandoffSuggestion(True, HandoffReason.DESCONTO),
            )
        )
        outputs = (
            OutboundMessage(
                "conv-a",
                Intent.APRESENTAR_COTACAO,
                ApresentarCotacao(quote, ProductFacts("completo", "Completo", ("roubo",), False)),
            ),
            OutboundMessage("conv-a", Intent.PEDIR_DADO, PedirDado("idade")),
            OutboundMessage("conv-a", Intent.RECUSAR, Declined("idade")),
            OutboundMessage("conv-a", Intent.ESCALAR, decision),
        )
        for output in outputs:
            identifier = await store.enqueue(output, "lead", now)
            assert await store.outbound(identifier) == output
        raw = json.loads(
            conn.execute("SELECT payload FROM outbound_messages ORDER BY rowid LIMIT 1").fetchone()[
                0
            ]
        )
        assert str(quote.premio_mensal) in json.dumps(raw)
        restored = await store.outbound(
            conn.execute("SELECT id FROM outbound_messages ORDER BY rowid LIMIT 1").fetchone()[0]
        )
        assert isinstance(restored.payload.quote.premio_mensal, Decimal)
        assert conn.execute("SELECT DISTINCT status FROM outbound_messages").fetchall() == [
            ("pendente",)
        ]
        handoff_id = await store.record_handoff("conv-a", decision, now)
        assert await store.handoff(handoff_id) == decision
        assert "01310100" not in "\n".join(conn.iterdump())
        assert conn.execute("SELECT sugerido_por_llm FROM handoffs").fetchone()[0] == 1
        assert decision.divergencia
        assert await store.handoff("missing") is None
        assert await store.outbound("missing") is None
        with pytest.raises(ValueError):
            await store.record_handoff(
                "conv-a", HandoffPolicy().evaluate(ConversationContext()), now
            )
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outbox", [False, True])
async def test_direct_handoff_is_redacted_before_storage(outbox: bool) -> None:
    from infrastructure.persistence.delivery import SQLiteDelivery

    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, tzinfo=UTC)
        await SQLiteConversations(conn).ensure(message(), None, now)
        store = SQLiteDelivery(conn)
        attempt = QuoteAttempt(
            "trace",
            "conv-a",
            "a" * 64,
            1,
            "unavailable",
            "api",
            503,
            5,
            False,
            False,
            "Falhou: pessoa@example.com",
            now,
        )
        snapshot = HandoffSnapshot(
            {
                "cep": CollectedSlot(1310100, "digitado"),
                "plano_id": CollectedSlot("pessoa@example.com", "digitado"),
            },
            (attempt,),
            HandoffReason.HUMANO,
        )
        decision = HandoffDecision(True, HandoffReason.HUMANO, None, True, snapshot)
        if outbox:
            identifier = await store.enqueue(
                OutboundMessage("conv-a", Intent.ESCALAR, decision), "lead", now
            )
            output = await store.outbound(identifier)
            assert output is not None and isinstance(output.payload, HandoffDecision)
            restored = output.payload
        else:
            identifier = await store.record_handoff("conv-a", decision, now)
            restored = await store.handoff(identifier)
        assert restored is not None and restored.snapshot is not None
        assert restored.snapshot.slots["cep"].valor == "[CEP REDIGIDO]"
        assert restored.snapshot.slots["plano_id"].valor == "[EMAIL]"
        assert restored.snapshot.tentativas[0].erro == "Falhou: [EMAIL]"
        assert snapshot.slots["cep"].valor == 1310100
        dump = "\n".join(conn.iterdump())
        assert "pessoa@example.com" not in dump and "1310100" not in dump
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_decline_redaction_preserves_quote_money(quote_payload: dict[str, Any]) -> None:
    from infrastructure.persistence.delivery import SQLiteDelivery

    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, tzinfo=UTC)
        await SQLiteConversations(conn).ensure(message(), None, now)
        store = SQLiteDelivery(conn)
        identifier = await store.enqueue(
            OutboundMessage(
                "conv-a", Intent.RECUSAR, Declined("Contate pessoa@example.com CEP 01310100")
            ),
            "lead",
            now,
        )
        output = await store.outbound(identifier)
        assert output.payload.motivo == "Contate [EMAIL] CEP [CEP]"
        quote = replace(
            Quote.from_api(quote_payload),
            premio_mensal=Decimal("12345678.99"),
            franquia=Decimal("12345678.00"),
        )
        output = OutboundMessage(
            "conv-a",
            Intent.APRESENTAR_COTACAO,
            ApresentarCotacao(quote, ProductFacts("completo", "Completo", ("roubo",), False)),
        )
        identifier = await store.enqueue(output, "lead", now)
        assert await store.outbound(identifier) == output
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outbox", [False, True])
async def test_unsupported_attempt_is_rejected_before_writing(outbox: bool) -> None:
    from infrastructure.persistence.delivery import SQLiteDelivery

    @dataclass(frozen=True)
    class OtherAttempt:
        tentativa: int = 1
        status: str = "quoted"
        latencia_ms: int = 1

    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, tzinfo=UTC)
        await SQLiteConversations(conn).ensure(message(), None, now)
        store = SQLiteDelivery(conn)
        decision = HandoffDecision(
            True,
            HandoffReason.HUMANO,
            None,
            True,
            HandoffSnapshot({}, (OtherAttempt(),), HandoffReason.HUMANO),
        )
        with pytest.raises(ValueError, match="Snapshot exige registros QuoteAttempt"):
            if outbox:
                await store.enqueue(
                    OutboundMessage("conv-a", Intent.ESCALAR, decision), "lead", now
                )
            else:
                await store.record_handoff("conv-a", decision, now)
        assert conn.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM outbound_messages").fetchone()[0] == 0
    finally:
        conn.close()
