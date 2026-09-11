from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import SalesGraph, TurnConfig
from agent.nodes.converse import ConversationResult, Converser
from agent.nodes.extract import ExtractionResult
from agent.schemas.slots import Slots
from agent.templates import render_safe_reply
from application.llm import LLMResponse, LLMToolCall
from domain.acceptance import AcceptanceRules
from domain.handoff import HandoffReason
from domain.product import ProductFacts
from domain.quote import Quote, QuoteUnavailable
from tests.fakes import CANONICAL_OBJECTIONS
from tests.virtual_time import virtual_time

FACTS = (ProductFacts("completo", "Completo", ("roubo", "furto", "colisao"), True),)


def slots(age=30, year=2020):
    return Slots.model_validate(
        {
            key: {"valor": value, "status": "informado", "proveniencia": "digitado"}
            for key, value in {
                "idade": age,
                "veiculo_ano": year,
                "plano_id": "completo",
                "data_inicio": "2026-09-01",
            }.items()
        }
    )


def build(
    clock,
    plans_payload,
    quote_payload,
    *,
    age=30,
    outcome=None,
    checkpointer=None,
    config=None,
    converser=None,
    recorder=None,
):
    extractor = AsyncMock(extract=AsyncMock(return_value=ExtractionResult(slots(age))))
    converser = converser or AsyncMock(
        converse=AsyncMock(return_value=ConversationResult("", None, "completo"))
    )
    quote = AsyncMock(quote=AsyncMock(return_value=outcome or Quote.from_api(quote_payload)))
    rules = AsyncMock(current=AsyncMock(return_value=AcceptanceRules.from_api(plans_payload)))
    handoff = AsyncMock()
    traces = AsyncMock(read=AsyncMock(return_value=()))
    graph = SalesGraph(
        extractor=extractor,
        converser=converser,
        quote=quote,
        rules=rules,
        products=FACTS,
        clock=clock,
        handoff=handoff,
        traces=traces,
        checkpointer=checkpointer or InMemorySaver(),
        config=config or TurnConfig(),
        recorder=recorder,
    )
    return graph, extractor, converser, quote, handoff


def test_eligible_conversation_quotes_and_templates(plans_payload, quote_payload):
    with virtual_time() as clock:
        graph, _, converse, quote, _ = build(clock, plans_payload, quote_payload)
        result = clock.run(graph.turn("c", "m1", "Tenho trinta anos"))
        assert result["status"] == "cotada"
        assert "Mensalidade: R$" in result["texto"]
        assert quote.quote.await_count == 1
        assert converse.converse.await_count >= 1
        assert result["tool_result"]["status"] == "cotado"


def test_total_budget_covers_all_stages_and_distinguishes_deadline(plans_payload, quote_payload):
    for budget, expected in ((6.0, "escalada"), (10.0, "cotada")):
        with virtual_time() as clock:
            graph, extractor, converse, quote, handoff = build(
                clock, plans_payload, quote_payload, config=TurnConfig(budget_seconds=budget)
            )

            async def extraction(*args, **kwargs):
                await clock.sleep(2.35)
                return ExtractionResult(slots())

            async def speech(*args, **kwargs):
                await clock.sleep(1.0)
                return ConversationResult("", None, "completo")

            async def quoting(*args, **kwargs):
                await clock.sleep(3.0)
                return Quote.from_api(quote_payload)

            extractor.extract.side_effect = extraction
            converse.converse.side_effect = speech
            quote.quote.side_effect = quoting
            result = clock.run(graph.turn("c", "m1", "Pode cotar"))
            assert result["status"] == expected
            assert result["tempos_ms"]["extract"] == pytest.approx(2350)
            if budget == 6:
                assert handoff.call_args.args[2].motivo is HandoffReason.PRAZO


def test_ineligible_never_calls_converser_or_quote(plans_payload, quote_payload):
    with virtual_time() as clock:
        graph, _, converse, quote, handoff = build(clock, plans_payload, quote_payload, age=100)
        result = clock.run(graph.turn("c", "m1", "Tenho cem anos"))
        assert result["status"] == "recusada"
        assert "Motivo" in result["texto"]
        converse.converse.assert_not_called()
        quote.quote.assert_not_called()
        handoff.assert_not_called()


def test_direct_turn_cannot_silently_drop_collected_cep(plans_payload, quote_payload):
    with virtual_time() as clock:
        graph, _, _, quote, _ = build(clock, plans_payload, quote_payload)
        with pytest.raises(RuntimeError, match="privado"):
            clock.run(graph.turn("c", "m1", "CEP 01310-100"))
        quote.quote.assert_not_called()


def test_quote_failure_handoff_has_slots(plans_payload, quote_payload):
    with virtual_time() as clock:
        graph, _, _, quote, handoff = build(clock, plans_payload, quote_payload)
        quote.quote.side_effect = QuoteUnavailable(tentativas=3)
        result = clock.run(graph.turn("c", "m1", "Tenho trinta anos"))
        assert result["status"] == "escalada"
        decision = handoff.call_args.args[2]
        assert decision.motivo is HandoffReason.COTACAO
        assert decision.snapshot.slots["idade"].valor == 30


def test_objection_routes_without_quote_and_next_message_returns(plans_payload, quote_payload):
    with virtual_time() as clock:
        graph, _, _, quote, _ = build(clock, plans_payload, quote_payload)
        result = clock.run(graph.turn("c", "m1", "Está caro", objecoes_preco=1))
        assert result["status"] == "ativa"
        assert "objection" in result["rota"]
        quote.quote.assert_not_called()
        result = clock.run(graph.turn("c", "m2", "Pode cotar", objecoes_preco=1))
        assert result["status"] == "cotada"


def speaking(*replies):
    responses = [
        LLMResponse("", "m", 1, 2, None, 0, (reply,))
        if isinstance(reply, LLMToolCall)
        else LLMResponse(json.dumps({"texto": reply, "escalacao": None}), "m", 1, 2, None, 0)
        for reply in replies
    ]
    return AsyncMock(complete=AsyncMock(side_effect=responses))


def recorded(recorder):
    return {call.args[0].etapa: call.args[0] for call in recorder.record.call_args_list}


def test_canonical_objections_and_counteroffer_reach_converser(plans_payload, quote_payload):
    lead = (*CANONICAL_OBJECTIONS, "consigo por 180 na concorrente")
    leaf = speaking(*["Entendo." for _ in lead])
    with virtual_time() as clock:
        graph, *_ = build(clock, plans_payload, quote_payload, converser=Converser(leaf))
        for index, text in enumerate(lead):
            clock.run(graph.turn("c", f"m{index}", text))
    assert json.loads(leaf.complete.call_args.args[0].user)["historico"] == list(lead)


def test_money_in_model_speech_is_trace_event_and_template_reply(plans_payload, quote_payload):
    recorder = Mock()
    leaf = speaking("O Completo fica R$ 313,80 por mês.")
    with virtual_time() as clock:
        graph, _, _, quote, _ = build(
            clock, plans_payload, quote_payload, converser=Converser(leaf), recorder=recorder
        )
        result = clock.run(graph.turn("c", "m1", "quanto fica?"))
    assert result["status"] == "ativa"
    assert result["texto"] == render_safe_reply(FACTS)
    quote.quote.assert_not_called()
    event = recorded(recorder)["guardrail"]
    assert (event.status, event.erro) == ("violacao", "O Completo fica R$ 313,80 por mês.")


def test_invalid_plan_from_model_is_contract_event_not_refusal(plans_payload, quote_payload):
    recorder = Mock()
    leaf = speaking(LLMToolCall("cotar", {"plano_id": "platinum"}))
    with virtual_time() as clock:
        graph, _, _, quote, handoff = build(
            clock, plans_payload, quote_payload, converser=Converser(leaf), recorder=recorder
        )
        result = clock.run(graph.turn("c", "m1", "quero o platinum"))
    quote.quote.assert_not_called()
    handoff.assert_not_called()
    assert result["status"] == "ativa"
    assert result["texto"] == render_safe_reply(FACTS)
    assert recorded(recorder)["converse"].erro == "contrato_llm"
