from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from agent.nodes.converse import ConversationInput, Converser, project_quote
from agent.templates import format_brl, render_safe_reply
from application.llm import LLMContractError, LLMResponse, LLMToolCall
from domain.handoff import HandoffReason
from domain.objection import Objecao
from domain.quantidade import contem_quantidade
from domain.quote import Declined, Quote, QuoteUnavailable
from infrastructure.planos.projections import project_planos
from tests.fakes import CANONICAL_OBJECTIONS


@pytest.fixture
def products(plans_payload):
    return project_planos(plans_payload).product_facts


DEFAULT = '{"texto":"Posso ajudar.","escalacao":null,"objecao":"nenhuma","assunto":"seguro_auto"}'


def client(content=DEFAULT, calls=()):
    return AsyncMock(
        complete=AsyncMock(return_value=LLMResponse(content, "m", 1, 2, None, 0, calls))
    )


def speech(text, escalacao=None, objecao="nenhuma", assunto="seguro_auto"):
    content = {"texto": text, "escalacao": escalacao, "objecao": objecao, "assunto": assunto}
    return client(json.dumps(content))


@pytest.mark.asyncio
async def test_subject_category_is_required_and_reaches_the_result(products):
    leaf = speech("Entendo.", assunto="sinistro")
    result = await converse(leaf, products)
    assert result.assunto == "sinistro"
    assert "assunto" in leaf.complete.call_args.args[0].schema["required"]


@pytest.mark.asyncio
async def test_subject_outside_the_enum_is_contract_error(products):
    with pytest.raises(LLMContractError):
        await converse(speech("Certo.", assunto="seguro_de_vida"), products)


async def converse(leaf, products, historico=(), resultado=None):
    return await Converser(leaf).converse(
        ConversationInput("c", "Vendedor cordial", tuple(historico), products, resultado)
    )


@pytest.mark.asyncio
async def test_tool_schema_is_closed_enum_of_catalog_plans(products):
    leaf = client("", (LLMToolCall("cotar", {"plano_id": "premium"}),))
    result = await converse(leaf, products)
    assert result.plano_id == "premium"
    parameters = leaf.complete.call_args.args[0].tools[0].parameters
    assert set(parameters["properties"]) == {"plano_id"}
    assert parameters["properties"]["plano_id"]["enum"] == ["essencial", "completo", "premium"]


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", ["platinum", "Premium", "premium ", "", 3])
async def test_plan_outside_catalog_is_contract_error(products, plan):
    with pytest.raises(LLMContractError):
        await converse(client("", (LLMToolCall("cotar", {"plano_id": plan}),)), products)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        LLMToolCall("cotar", {"plano_id": "premium", "cep": "01310100"}),
        LLMToolCall("outra", {"plano_id": "premium"}),
    ],
)
async def test_tool_rejects_extra_arguments_and_unknown_names(products, call):
    with pytest.raises(LLMContractError):
        await converse(client("", (call,)), products)


@pytest.mark.asyncio
async def test_context_carries_projection_never_pricing(products, plans_payload, quote_payload):
    quote = Quote.from_api(quote_payload)
    facts = next(item for item in products if item.plano_id == quote.plano_id)
    leaf = client()
    await converse(leaf, products, ("Quero cotar",), project_quote(quote, facts))
    request = leaf.complete.call_args.args[0]
    context = (request.system + request.user).lower()
    amounts = [quote.premio_mensal, quote.franquia]
    amounts += [Decimal(str(plan["base_mensal"])) for plan in plans_payload["planos"]]
    for amount in amounts:
        assert str(amount) not in context
        assert format_brl(amount).lower() not in context
    for forbidden in ("base_mensal", "multiplicador", "premio_mensal", "franquia"):
        assert forbidden not in context


@pytest.mark.asyncio
async def test_lead_speech_reaches_converser_intact_after_pii_redaction(products):
    history = (*CANONICAL_OBJECTIONS, "consigo por 180 na concorrente", "meu cpf é 529.982.247-25")
    leaf = client()
    await converse(leaf, products, history)
    sent = json.loads(leaf.complete.call_args.args[0].user)["historico"]
    assert sent == [*CANONICAL_OBJECTIONS, "consigo por 180 na concorrente", "meu cpf é [CPF]"]


@pytest.mark.asyncio
async def test_suggestion_is_enum_and_invalid_schema_is_contract_error(products):
    result = await converse(speech("Entendi.", "pedido_de_humano"), products)
    assert result.escalacao is HandoffReason.HUMANO
    with pytest.raises(LLMContractError):
        await converse(
            client('{"texto":"Olá","escalacao":"vou chamar alguém","objecao":"nenhuma"}'), products
        )


@pytest.mark.asyncio
async def test_objection_is_required_structured_enum_alongside_speech(products):
    leaf = speech("Entendo.", objecao="franquia_alta")
    assert (await converse(leaf, products)).objecao is Objecao.FRANQUIA_ALTA
    assert "objecao" in leaf.complete.call_args.args[0].schema["required"]
    assert (await converse(speech("Certo."), products)).objecao is None
    with pytest.raises(LLMContractError):
        await converse(speech("Certo.", objecao="achei caro"), products)
    with pytest.raises(LLMContractError):
        await converse(client('{"texto":"Certo.","escalacao":null}'), products)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "O Premium inclui assistência 24h e carro reserva.",
        "Entendo, a franquia pesou. Posso mostrar outro plano.",
        "A carência vale para roubo e furto e conta do início da vigência.",
        "Um plano mais enxuto pode fazer sentido para você.",
    ],
)
async def test_number_free_speech_and_objection_replies_pass(products, text):
    result = await converse(speech(text), products)
    assert result.texto == text
    assert result.violacao is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Fica R$ 313,80 por mês.",
        "Custa trezentos reais.",
        "Sai por 313,80.",
        "Pode pagar dez por mês.",
        "Custa onze por mês.",
        "Você paga doze.",
        "Sai por quinze.",
        "Fica por dezoito mensais.",
        "A franquia é de 1.000.",
        "O seguro custa cinco reais por mês.",
        "Consigo desconto de 15% no Premium.",
        "O primeiro mês é grátis.",
        # Número fora do payload também é inventado pelo modelo (D-042).
        "Carência de 30 dias para roubo e furto.",
        "São 7 coberturas no Premium, contra 3 no Essencial.",
        "A vigência pode começar em 01/10/2026.",
    ],
)
async def test_speech_with_any_quantity_falls_back_to_template_and_is_reported(products, text):
    result = await converse(speech(text), products)
    assert result.texto == render_safe_reply(products)
    assert result.violacao == text
    assert not contem_quantidade(result.texto)


def test_decline_and_unavailable_keep_distinct_projection(products):
    facts = products[0]
    assert project_quote(Declined("idade fora"), facts)["status"] == "recusado"
    assert project_quote(QuoteUnavailable(), facts)["status"] == "indisponivel"


@pytest.mark.asyncio
async def test_new_turn_can_request_other_plan_after_previous_quote(products):
    leaf = client("", (LLMToolCall("cotar", {"plano_id": "premium"}),))
    result = await converse(
        leaf,
        products,
        ("Quero o premium agora.",),
        {"status": "cotado", "nome_plano": "Completo", "coberturas": ["roubo"], "carencia": True},
    )
    assert result.plano_id == "premium"
    assert leaf.complete.call_args.args[0].tools[0].name == "cotar"
