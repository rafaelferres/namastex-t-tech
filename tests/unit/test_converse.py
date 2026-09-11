from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agent.nodes.converse import ConversationInput, Converser, project_quote
from application.llm import LLMContractError, LLMResponse, LLMToolCall
from domain.handoff import HandoffReason
from domain.product import ProductFacts
from domain.quote import Declined, Quote, QuoteUnavailable

FACTS = ProductFacts("basico", "Básico", ("roubo", "furto"), True)


def client(content='{"texto":"Posso ajudar.","escalacao":null}', calls=()):
    return AsyncMock(
        complete=AsyncMock(return_value=LLMResponse(content, "m", 1, 2, None, 0, calls))
    )


@pytest.mark.asyncio
async def test_tool_has_only_plan_and_no_pricing_in_context(quote_payload):
    leaf = client("", (LLMToolCall("cotar", {"plano_id": "basico"}),))
    node = Converser(leaf)
    result = await node.converse(
        ConversationInput(
            "conv",
            "Vendedor cordial",
            ("O prêmio era R$ 313,80 e a franquia R$ 1.000,00.",),
            (FACTS,),
            None,
        )
    )
    assert result.plano_id == "basico"
    request = leaf.complete.call_args.args[0]
    assert set(request.tools[0].parameters["properties"]) == {"plano_id"}
    context = request.system + request.user
    for forbidden in (
        "313",
        "1.000",
        "premio",
        "prêmio",
        "franquia",
        "base_mensal",
        "multiplicador",
    ):
        assert forbidden not in context.lower()
    projection = project_quote(Quote.from_api(quote_payload), FACTS)
    assert set(projection) == {"status", "nome_plano", "coberturas", "carencia"}
    assert projection["status"] == "cotado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        LLMToolCall("cotar", {"plano_id": "basico", "cep": "01310100"}),
        LLMToolCall("outra", {"plano_id": "basico"}),
    ],
)
async def test_tool_rejects_extra_arguments_and_unknown_names(call):
    with pytest.raises(LLMContractError):
        await Converser(client("", (call,))).converse(ConversationInput("c", "", (), (FACTS,)))


@pytest.mark.asyncio
async def test_suggestion_is_enum_and_invalid_prose_rejected():
    result = await Converser(
        client(json.dumps({"texto": "Entendi.", "escalacao": "pedido_de_humano"}))
    ).converse(ConversationInput("c", "", (), (FACTS,)))
    assert result.escalacao is HandoffReason.HUMANO
    with pytest.raises(LLMContractError):
        await Converser(client('{"texto":"Olá","escalacao":"vou chamar alguém"}')).converse(
            ConversationInput("c", "", (), (FACTS,))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["Custa R$ 300,00", "Custa trezentos reais", "Pode pagar dez por mês"]
)
async def test_free_speech_cannot_publish_numbers(text):
    with pytest.raises(LLMContractError):
        await Converser(client(json.dumps({"texto": text, "escalacao": None}))).converse(
            ConversationInput("c", "", (), (FACTS,))
        )


def test_decline_and_unavailable_keep_distinct_projection():
    assert project_quote(Declined("idade fora"), FACTS)["status"] == "recusado"
    assert project_quote(QuoteUnavailable(), FACTS)["status"] == "indisponivel"
