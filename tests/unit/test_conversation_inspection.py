"""Inspeção por conversa: o log de execução sai do que o sistema persistiu (tarefa 11)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.inspect_conversation import InspectConversation, SlotView, TurnReport
from application.tracing import QuoteAttempt
from application.turns import TurnEvent
from domain.handoff import CollectedSlot, HandoffDecision, HandoffReason, HandoffSnapshot
from domain.messages import InboundMessage, Intent, MensagemConversacional, OutboundMessage
from infrastructure.persistence.checkpoint import CheckpointTurnStates
from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.persistence.delivery import SQLiteDelivery
from interfaces.conversation_report import render_conversation
from tests.unit.test_graph import build
from tests.virtual_time import virtual_time

NOW = datetime(2026, 9, 11, 10)


def attempt(trace, tentativa, status, http, *, hedge=False, latency=40):
    return QuoteAttempt(
        trace, "c", "fp", tentativa, status, "api", http, latency, hedge, False, None, NOW
    )


def event(trace, etapa, status="ativa", latency=10, erro=None):
    return TurnEvent(trace, "c", etapa, status, latency, erro, NOW)


SNAPSHOT = HandoffSnapshot(
    {"idade": CollectedSlot(30, "digitado")},
    (attempt("c:m2", 1, "unavailable", 503),),
    HandoffReason.COTACAO,
)
DECISION = HandoffDecision(True, HandoffReason.COTACAO, None, False, SNAPSHOT)
REPLY = OutboundMessage("c", Intent.CONVERSAR, MensagemConversacional("Qual o ano do carro?"))


def state(trace, entrada, status="ativa", **extra):
    slots = {
        "idade": {"valor": 30, "status": "informado", "proveniencia": "digitado"},
        "veiculo_ano": None,
    }
    return {"trace_id": trace, "entrada": entrada, "status": status, "slots": slots, **extra}


@pytest.mark.asyncio
async def test_use_case_assembles_each_turn_from_persisted_records():
    turns_states = (
        state("c:m1", "Tenho 30 anos", rota=["extract", "policy"]),
        state("c:m2", "Pode cotar", "escalada", erro="quote"),
    )
    reports = await InspectConversation(
        AsyncMock(read_states=AsyncMock(return_value=turns_states)),
        AsyncMock(read=AsyncMock(side_effect=lambda t: SNAPSHOT.tentativas if t == "c:m2" else ())),
        AsyncMock(read_turn=AsyncMock(side_effect=lambda t: (event(t, "extract"),))),
        AsyncMock(lead_messages=AsyncMock(return_value={"c:m1": REPLY})),
        AsyncMock(handoff=AsyncMock(side_effect=lambda t: DECISION if t == "c:m2" else None)),
    ).execute("c")
    first, second = reports
    assert (first.trace_id, first.entrada, first.resposta, first.escalacao) == (
        "c:m1",
        "Tenho 30 anos",
        REPLY,
        None,
    )
    assert first.slots == (SlotView("idade", "30", "informado", "digitado"),)
    assert first.rota == ("extract", "policy")
    assert first.etapas == (event("c:m1", "extract"),)
    assert (second.status, second.erro, second.resposta) == ("escalada", "quote", None)
    assert (second.tentativas, second.escalacao) == (SNAPSHOT.tentativas, DECISION)


@pytest.mark.asyncio
async def test_lead_messages_are_keyed_by_turn_including_the_handoff_notice(tmp_path):
    connection = connect(tmp_path / "agent.sqlite")
    try:
        await SQLiteConversations(connection).ensure(
            InboundMessage("replay", "c", "c", "text", "", "m1", 0), None, NOW
        )
        delivery = SQLiteDelivery(connection)
        await delivery.enqueue(REPLY, "lead", NOW, identifier="c:m1:reply")
        await delivery.enqueue_handoff("c", DECISION, NOW, identifier="c:m2")
        messages = await delivery.lead_messages("c")
    finally:
        connection.close()
    assert messages["c:m1"] == REPLY
    assert messages["c:m2"].intent is Intent.ESCALAR
    assert set(messages) == {"c:m1", "c:m2"}  # webhook e fila não são mensagem ao lead


def test_checkpoints_give_the_final_state_of_each_turn_in_order(plans_payload, quote_payload):
    saver = InMemorySaver()
    with virtual_time() as clock:
        graph, *_ = build(clock, plans_payload, quote_payload, checkpointer=saver)
        clock.run(graph.turn("c", "m1", "Tenho trinta anos"))
        clock.run(graph.turn("c", "m2", "Pode cotar"))
        states = clock.run(CheckpointTurnStates(saver).read_states("c"))
    assert [item["trace_id"] for item in states] == ["c:m1", "c:m2"]
    assert [item["entrada"] for item in states] == ["Tenho trinta anos", "Pode cotar"]
    assert all(item["rota"][-1] == "present" for item in states)


def test_report_shows_the_model_opinion_next_to_the_policy_decision():
    opinion = TurnEvent(
        "c:m1", "c", "decisao", "segue", 0, None, NOW, sugestao="pedido_de_humano"
    )
    report = TurnReport(
        trace_id="c:m1",
        entrada="Oi",
        slots=(),
        rota=("extract", "policy", "converse"),
        status="ativa",
        pedido=None,
        erro=None,
        objecao=None,
        objecao_fonte=None,
        etapas=(opinion,),
        tentativas=(),
        resposta=REPLY,
        escalacao=None,
    )
    text = render_conversation("c", (report,))
    assert "- Conversador: sugeriu `pedido_de_humano`; a política não escalou (divergência)" in text


def test_report_is_readable_markdown_with_attempts_hedge_and_snapshot():
    report = TurnReport(
        trace_id="c:m2",
        entrada="Pode cotar, cep [CEP]",
        slots=(SlotView("idade", "30", "informado", "digitado"),),
        rota=("extract", "policy", "converse", "quote", "present", "handoff"),
        status="escalada",
        pedido=None,
        erro="quote",
        objecao=None,
        objecao_fonte=None,
        etapas=(event("c:m2", "quote", latency=3500, erro="quote"),),
        tentativas=(
            attempt("c:m2", 1, "unavailable", 503),
            attempt("c:m2", 2, "quoted", 200, hedge=True, latency=900),
            attempt("c:m2", 3, "unavailable", None, latency=120),
            attempt("c:m2", 0, "unavailable", None, latency=3500),
        ),
        resposta=OutboundMessage("c", Intent.ESCALAR, DECISION),
        escalacao=DECISION,
    )
    text = render_conversation("c", (report,))
    assert "### Turno 1" in text
    assert "> Pode cotar, cep [CEP]" in text
    assert "| idade | 30 | informado | digitado |" in text
    assert "| 1 | unavailable | 503 | 40 ms | api | não |" in text
    assert "| 2 | quoted | 200 | 900 ms | api | sim |" in text
    # Chamada física sem HTTP foi abandonada (hedge venceu) ou estourou: não é "—".
    assert "| 3 | unavailable | sem resposta | 120 ms | api | não |" in text
    assert "| desfecho | unavailable | — | 3500 ms | api | não |" in text
    assert "cotacao_esgotada" in text
    assert "Snapshot enviado ao vendedor" in text
    assert "01310" not in text
