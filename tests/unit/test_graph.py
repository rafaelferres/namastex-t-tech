from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import SalesGraph, TurnConfig
from agent.nodes.converse import ConversationResult, Converser
from agent.nodes.extract import ExtractionResult
from agent.schemas.slots import Slots
from agent.templates import render_objection, render_safe_reply
from application.llm import LLMConfigurationError, LLMResponse, LLMToolCall, LLMUnavailable
from domain.acceptance import AcceptanceRules
from domain.handoff import HandoffReason
from domain.objection import Objecao
from domain.product import ProductFacts
from domain.quote import Quote, QuoteUnavailable
from tests.fakes import CANONICAL_OBJECTIONS
from tests.unit.test_objection import EXPECTED
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
    traces = AsyncMock(read_conversation=AsyncMock(return_value=()))
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
        graph, _, converse, quote, _ = build(clock, plans_payload, quote_payload)
        converse.converse.side_effect = [
            ConversationResult("Entendo.", None),
            ConversationResult("", None, "completo"),
        ]
        result = clock.run(graph.turn("c", "m1", "Está caro", objecoes_preco=1))
        assert result["status"] == "ativa"
        assert "objection" in result["rota"]
        quote.quote.assert_not_called()
        result = clock.run(graph.turn("c", "m2", "Pode cotar", objecoes_preco=1))
        assert result["status"] == "cotada"


def speaking(*replies):
    """Texto, (texto, objeção) ou chamada de tool; objeção padrão é "nenhuma"."""
    responses = []
    for reply in replies:
        if isinstance(reply, LLMToolCall):
            responses.append(LLMResponse("", "m", 1, 2, None, 0, (reply,)))
            continue
        text, objection = reply if isinstance(reply, tuple) else (reply, "nenhuma")
        content = json.dumps({"texto": text, "escalacao": None, "objecao": objection})
        responses.append(LLMResponse(content, "m", 1, 2, None, 0))
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


OBJECTION_TEXTS = [
    *CANONICAL_OBJECTIONS,
    *(f"{text}... a Porto Seguro me ofereceu menos" for text in CANONICAL_OBJECTIONS),
]


@pytest.mark.parametrize("text", OBJECTION_TEXTS)
def test_objection_reaches_node_through_floor_when_model_says_none(
    plans_payload, quote_payload, text
):
    with virtual_time() as clock:
        graph, _, _, quote, _ = build(
            clock, plans_payload, quote_payload, converser=Converser(speaking("Entendo."))
        )
        result = clock.run(graph.turn("c", "m1", text))
    category = EXPECTED[text.split("...")[0]]
    assert result["rota"][-1] == "objection"
    assert (result["objecao"], result["objecao_fonte"]) == (category.value, "lexico")
    assert result["texto"] == render_objection(category)
    quote.quote.assert_not_called()


def test_model_classified_objection_without_keyword_reaches_node(plans_payload, quote_payload):
    leaf = speaking(("Entendo.", "preco_alto"))
    with virtual_time() as clock:
        graph, *_ = build(clock, plans_payload, quote_payload, converser=Converser(leaf))
        result = clock.run(graph.turn("c", "m1", "esperava pagar menos"))
    assert result["rota"][-1] == "objection"
    assert (result["objecao"], result["objecao_fonte"]) == ("preco_alto", "modelo")
    assert result["texto"] == render_objection(Objecao.PRECO_ALTO)


def test_llm_failure_body_reaches_the_turn_trace(plans_payload, quote_payload):
    recorder = Mock()
    failure = LLMUnavailable(detalhe="HTTP 503: sobrecarga do provedor")
    leaf = AsyncMock(complete=AsyncMock(side_effect=failure))
    with virtual_time() as clock:
        graph, *_ = build(
            clock, plans_payload, quote_payload, converser=Converser(leaf), recorder=recorder
        )
        result = clock.run(graph.turn("c", "m1", "Oi"))
    assert result["status"] == "escalada"
    event = recorded(recorder)["converse_falha"]
    assert "HTTP 503: sobrecarga do provedor" in event.erro


def test_llm_configuration_error_is_traced_and_fails_the_turn(plans_payload, quote_payload):
    recorder = Mock()
    failure = LLMConfigurationError("openrouter", "HTTP 404: No endpoints found")
    leaf = AsyncMock(complete=AsyncMock(side_effect=failure))
    with virtual_time() as clock:
        graph, *_ = build(
            clock, plans_payload, quote_payload, converser=Converser(leaf), recorder=recorder
        )
        with pytest.raises(LLMConfigurationError):
            clock.run(graph.turn("c", "m1", "Oi"))
    assert "HTTP 404: No endpoints found" in recorded(recorder)["converse_falha"].erro
    assert leaf.complete.await_count == 1  # configuração não melhora na segunda tentativa


def slow_then(clock, seconds, result, *, times=1):
    """Duplo que dorme além do teto nas primeiras `times` chamadas e depois responde."""
    calls = 0

    async def call(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= times:
            await clock.sleep(seconds)
        return result

    return call


def test_extraction_over_ceiling_is_retried_once_and_turn_completes(plans_payload, quote_payload):
    recorder = Mock()
    config = TurnConfig(budget_seconds=30.0, extraction_seconds=2.0)
    with virtual_time() as clock:
        graph, extractor, *_ = build(
            clock, plans_payload, quote_payload, config=config, recorder=recorder
        )
        extractor.extract.side_effect = slow_then(clock, 5.0, ExtractionResult(slots()))
        result = clock.run(graph.turn("c", "m1", "Tenho trinta anos"))
    assert result["status"] == "cotada"
    assert extractor.extract.await_count == 2
    assert recorded(recorder)["extract_falha"].status == "TimeoutError"
    assert result["tempos_ms"]["extract"] == pytest.approx(2000)


def test_second_llm_timeout_escalates_as_llm_without_third_attempt(plans_payload, quote_payload):
    config = TurnConfig(budget_seconds=30.0, conversation_seconds=2.0)
    with virtual_time() as clock:
        graph, _, converse, quote, handoff = build(
            clock, plans_payload, quote_payload, config=config
        )
        converse.converse.side_effect = slow_then(
            clock, 5.0, ConversationResult("", None, "completo"), times=2
        )
        result = clock.run(graph.turn("c", "m1", "Pode cotar"))
    assert (result["status"], result["erro"]) == ("escalada", "llm")
    assert converse.converse.await_count == 2
    assert handoff.call_args.args[2].motivo is HandoffReason.LINGUAGEM
    quote.quote.assert_not_called()


def test_llm_retry_never_outlives_the_turn_budget(plans_payload, quote_payload):
    config = TurnConfig(budget_seconds=6.0, conversation_seconds=4.0)
    with virtual_time() as clock:
        graph, _, converse, *_ = build(clock, plans_payload, quote_payload, config=config)
        converse.converse.side_effect = slow_then(
            clock, 10.0, ConversationResult("", None, "completo"), times=2
        )
        result = clock.run(graph.turn("c", "m1", "Pode cotar"))
        elapsed = clock.monotonic()
    assert (result["status"], result["erro"]) == ("escalada", "prazo")
    assert converse.converse.await_count == 2
    assert elapsed == pytest.approx(6.0)


def test_fast_transient_llm_failure_is_retried(plans_payload, quote_payload):
    failure = LLMUnavailable(detalhe="HTTP 503: sobrecarga do provedor")
    speech = speaking("Posso ajudar com a cotação.").complete.side_effect
    leaf = AsyncMock(complete=AsyncMock(side_effect=[failure, *speech]))
    with virtual_time() as clock:
        graph, *_ = build(clock, plans_payload, quote_payload, converser=Converser(leaf))
        result = clock.run(graph.turn("c", "m1", "Oi"))
    assert result["status"] == "ativa"
    assert result["texto"] == "Posso ajudar com a cotação."
    assert leaf.complete.await_count == 2


def test_message_without_objection_does_not_route_to_node(plans_payload, quote_payload):
    leaf = speaking("Posso ajudar com a cotação.")
    with virtual_time() as clock:
        graph, *_ = build(clock, plans_payload, quote_payload, converser=Converser(leaf))
        result = clock.run(graph.turn("c", "m1", "Oi, queria fazer um seguro pro meu carro"))
    assert "objection" not in result["rota"]
    assert result["objecao"] is None
    assert result["texto"] == "Posso ajudar com a cotação."
