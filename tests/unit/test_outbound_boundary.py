"""A invariante do projeto vive no texto que o lead recebe, não na projeção."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from itertools import count
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import SalesGraph, TurnConfig
from agent.nodes.converse import Converser
from agent.nodes.extract import ExtractionResult
from agent.schemas.slots import Slots
from application.ingest import IngestedTurn
from application.llm import LLMResponse, LLMToolCall
from application.sales import SalesSession
from application.tracing import Correlation
from domain.acceptance import AcceptanceRules
from domain.messages import InboundMessage
from domain.quote import Declined, Quote, QuoteUnavailable
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.persistence.delivery import SQLiteDelivery
from infrastructure.persistence.quote_cache import SQLiteQuoteCache
from infrastructure.planos.projections import project_planos
from infrastructure.tracing.correlation import ContextCorrelationProvider
from infrastructure.tracing.recorder import BufferedAttemptRecorder
from infrastructure.wiring import build_quote_provider
from interfaces.rendering import render_outbound
from tests.fakes import FakeClock

# Independente do detector de produção: formato de dinheiro que um template emite.
MONEY = re.compile(r"R\$\s*([\d.]+,\d{2})")
ANY_MONEY = re.compile(r"R\$|\d,\d{2}\b|\breais\b", re.IGNORECASE)
INSTABILITY = re.compile(r"instabil|indispon|falha|tente novamente|sistema|t[ée]cnic", re.I)
DEADLINE = re.compile(
    r"\b(?:minutos?|horas?|hoje|amanh[ãa]|em breve|logo|prazo|at[ée] \d)\b", re.IGNORECASE
)


def amounts(text: str) -> set[Decimal]:
    return {Decimal(raw.replace(".", "").replace(",", ".")) for raw in MONEY.findall(text)}


def payload_amounts(payload: dict[str, object]) -> set[Decimal]:
    values = {Decimal(str(payload["premio_mensal"])), Decimal(str(payload["franquia"]))}
    first = payload.get("primeiro_pagamento_pro_rata")
    if isinstance(first, dict):
        values.add(Decimal(str(first["valor_primeiro_pagamento"])))
    return values


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


def llm(*replies: str | LLMToolCall) -> AsyncMock:
    responses = [
        LLMResponse("", "m", 1, 2, None, 0, (reply,))
        if isinstance(reply, LLMToolCall)
        else LLMResponse(json.dumps({"texto": reply, "escalacao": None}), "m", 1, 2, None, 0)
        for reply in replies
    ]
    return AsyncMock(complete=AsyncMock(side_effect=responses))


def inbound(conversation_id: str, text: str, index: int) -> IngestedTurn:
    message = InboundMessage(
        "replay", conversation_id, "lead", "text", text, f"{conversation_id}:{index}", index
    )
    return IngestedTurn(conversation_id, (message,), 0)


def graph_with(plans_payload, *, leaf, quote, age=30):
    return SalesGraph(
        extractor=AsyncMock(extract=AsyncMock(return_value=ExtractionResult(slots(age)))),
        converser=Converser(leaf),
        quote=quote,
        rules=AsyncMock(current=AsyncMock(return_value=AcceptanceRules.from_api(plans_payload))),
        products=project_planos(plans_payload).product_facts,
        clock=FakeClock(),
        handoff=AsyncMock(),
        traces=AsyncMock(read=AsyncMock(return_value=())),
        checkpointer=InMemorySaver(),
        config=TurnConfig(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [100, 30], ids=["regra_local", "api"])
async def test_refusal_reaches_lead_without_money_or_instability(plans_payload, age):
    quote = AsyncMock(quote=AsyncMock(return_value=Declined("Perfil fora da política")))
    leaf = llm(LLMToolCall("cotar", {"plano_id": "completo"}))
    graph = graph_with(plans_payload, leaf=leaf, quote=quote, age=age)
    text = render_outbound(await graph.respond(inbound("c", "Quero cotar", 0)))
    assert text.startswith("Não podemos oferecer")
    assert not ANY_MONEY.search(text)
    assert not INSTABILITY.search(text)


@pytest.mark.asyncio
async def test_unavailable_reaches_lead_without_money_or_deadline_promise(plans_payload):
    quote = AsyncMock(quote=AsyncMock(side_effect=QuoteUnavailable(tentativas=3)))
    tool = LLMToolCall("cotar", {"plano_id": "completo"})
    graph = graph_with(plans_payload, leaf=llm(tool, tool), quote=quote)
    state = await graph.turn("c", "m0", "Quero cotar")
    text = render_outbound(await graph.respond(inbound("c", "Pode seguir", 1)))
    for lead_text in (state["texto"], text):
        assert not ANY_MONEY.search(lead_text)
        assert not DEADLINE.search(lead_text)


@pytest.mark.asyncio
async def test_quote_message_amounts_equal_api_payload(plans_payload, quote_payload):
    quote = AsyncMock(quote=AsyncMock(return_value=Quote.from_api(quote_payload)))
    graph = graph_with(
        plans_payload, leaf=llm(LLMToolCall("cotar", {"plano_id": "completo"})), quote=quote
    )
    text = render_outbound(await graph.respond(inbound("c", "Quero o completo", 0)))
    assert amounts(text) == payload_amounts(quote_payload)
    other_bases = {
        Decimal(str(plan["base_mensal"]))
        for plan in plans_payload["planos"]
        if plan["id"] != quote_payload["plano_id"]
    }
    assert not amounts(text) & other_bases
    assert "multiplicador" not in text.lower() and "base" not in text.lower()


@pytest.mark.asyncio
async def test_every_outbound_carries_money_only_after_quoted_attempt(
    tmp_path, plans_payload, quote_payload
):
    """Conversa inteira: dinheiro no outbox exige linha quoted em quote_attempts."""
    path = tmp_path / "boundary.sqlite"
    store_conn, trace_conn, cache_conn = connect(path), connect(path), connect(path)
    conversation = "conv-boundary"
    clock = FakeClock()

    async def never(_: float) -> None:
        await asyncio.Future()

    sequence = count(1)
    correlation = ContextCorrelationProvider(
        lambda: Correlation(f"quote-{next(sequence)}", conversation)
    )
    try:
        await SQLiteConversations(store_conn).ensure(
            InboundMessage("replay", conversation, "lead", "text", "", "seed", 0), None, clock.now()
        )
        attempts = SQLiteAttempts(trace_conn)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=quote_payload)),
            base_url="https://quote.test",
        ) as client:
            rules = AcceptanceRules.from_api(plans_payload)
            chain = build_quote_provider(
                client=client,
                cache=SQLiteQuoteCache(cache_conn, clock),
                rules=AsyncMock(current=AsyncMock(return_value=rules)),
                clock=clock,
                sleep=never,
                rng=lambda: 0.5,
                recorder=BufferedAttemptRecorder(attempts.record),
                correlation=correlation,
            )
            leaf = llm(
                "Olá! O Completo fica R$ 99,90 por mês.",  # modelo tenta cotar sozinho
                "Carência de 30 dias em roubo e furto.",
                LLMToolCall("cotar", {"plano_id": "completo"}),
                "Confirmando: você paga duzentos e nove reais.",  # repete valor por conta
            )
            graph = graph_with(plans_payload, leaf=leaf, quote=chain)
            delivery = SQLiteDelivery(store_conn)
            session = SalesSession(graph, delivery, clock)
            texts = (
                "Oi, quanto fica o seguro?",
                "e a carencia?",
                "achei caro pra esse carro",
                "quero o completo",
                "fechado!",
            )
            for index, text in enumerate(texts, start=1):
                await session.consume(inbound(conversation, text, index))

        rows = store_conn.execute(
            "SELECT id FROM outbound_messages WHERE conversation_id=? ORDER BY rowid",
            (conversation,),
        ).fetchall()
        rendered = [render_outbound(await delivery.outbound(row[0])) for row in rows]
        quoted = trace_conn.execute(
            "SELECT count(*) FROM quote_attempts WHERE conversation_id=? AND status='quoted'",
            (conversation,),
        ).fetchone()[0]
        assert len(rendered) == len(texts)
        with_money = [index for index, text in enumerate(rendered) if ANY_MONEY.search(text)]
        # Só a apresentação (4º turno) tem valor, e só depois de haver tentativa quoted.
        assert with_money == [3]
        assert quoted >= 1
        assert amounts(rendered[3]) == payload_amounts(quote_payload)
    finally:
        for connection in (store_conn, trace_conn, cache_conn):
            connection.close()
