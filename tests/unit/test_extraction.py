from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent.nodes.extract import SlotExtractor
from agent.schemas.slots import Slots, SlotValue
from application.llm import LLMContractError, LLMRequest, LLMResponse, LLMRole, TokenBudgetExceeded
from infrastructure.privacy import PrivacyRedactor


def test_absent_and_uncertain_are_distinct() -> None:
    slots = Slots(idade=SlotValue[int](valor=None, status="incerto", proveniencia="digitado"))
    assert slots.cep is None
    assert slots.idade is not None and slots.idade.status == "incerto"
    with pytest.raises(ValidationError):
        SlotValue[int](valor=None, status="informado", proveniencia="digitado")


@pytest.mark.parametrize("raw", ["7234567", "07234-567", "07.234-567", "07234567"])
def test_cep_normalizes_without_losing_leading_zero(raw: str) -> None:
    slots = Slots.model_validate(
        {"cep": {"valor": raw, "status": "informado", "proveniencia": "digitado"}}
    )
    assert slots.cep is not None and slots.cep.valor == "07234567"


def test_cep_rejects_integer_and_future_year_is_preserved() -> None:
    with pytest.raises(ValidationError):
        Slots.model_validate(
            {"cep": {"valor": 7234567, "status": "informado", "proveniencia": "digitado"}}
        )
    slots = Slots(
        veiculo_ano=SlotValue[int](valor=2090, status="informado", proveniencia="digitado")
    )
    assert slots.veiculo_ano is not None and slots.veiculo_ano.valor == 2090


class FakeClient:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = iter(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(response, "fake", 10, 5, None, 1.0)


def informed(value: object) -> dict[str, object]:
    return {"valor": value, "status": "informado", "proveniencia": "digitado"}


@pytest.mark.asyncio
async def test_private_cep_and_money_never_enter_request() -> None:
    client = FakeClient("{}")
    result = await SlotExtractor(client, PrivacyRedactor()).extract(
        "CEP 07.234-567, CPF 529.982.247-25, email a@example.com, prêmio R$ 199,90",
        Slots(),
        conversation_id="conv",
    )
    assert result.slots.cep is not None and result.slots.cep.valor == "07234567"
    request = client.requests[0]
    assert request.role == LLMRole.EXTRACTOR and request.budget == 2.5
    for secret in ("07234567", "07.234-567", "529.982.247-25", "a@example.com", "199,90"):
        assert secret not in request.user
    assert set(request.schema["required"]) == set(Slots.model_fields)


@pytest.mark.asyncio
async def test_fragments_accumulate_without_clearing_or_changing_cep() -> None:
    client = FakeClient(
        json.dumps({"idade": informed(35)}), json.dumps({"veiculo_ano": informed(2027)})
    )
    extractor = SlotExtractor(client, PrivacyRedactor())
    first = await extractor.extract("Tenho 35 anos, CEP 7234567", Slots(), conversation_id="conv")
    second = await extractor.extract(
        "Meu carro é modelo 2027, CEP 21987654", first.slots, conversation_id="conv"
    )
    assert second.slots.idade is not None and second.slots.idade.valor == 35
    assert second.slots.veiculo_ano is not None and second.slots.veiculo_ano.valor == 2027
    assert second.slots.cep == first.slots.cep
    assert "07234567" not in client.requests[1].user


@pytest.mark.asyncio
async def test_transcription_requires_confirmation_even_when_model_claims_informed() -> None:
    client = FakeClient(json.dumps({"idade": informed(35)}))
    result = await SlotExtractor(client, PrivacyRedactor()).extract(
        "Tenho 35 anos, CEP 07234567",
        Slots(),
        conversation_id="conv",
        proveniencia="transcrito",
    )
    assert result.slots.idade is not None
    assert result.slots.idade.status == "incerto"
    assert result.slots.idade.proveniencia == "transcrito"
    assert result.slots.cep is not None and result.slots.cep.status == "incerto"


@pytest.mark.asyncio
async def test_model_cannot_invent_private_cep() -> None:
    client = FakeClient(json.dumps({"cep": informed("07234567")}))
    result = await SlotExtractor(client, PrivacyRedactor()).extract(
        "olá", Slots(), conversation_id="conv"
    )
    assert result.slots.cep is None


@pytest.mark.asyncio
async def test_budget_exhaustion_preserves_state() -> None:
    client = FakeClient(TokenBudgetExceeded())
    slots = Slots(idade=SlotValue[int](valor=35, status="informado", proveniencia="digitado"))
    result = await SlotExtractor(client, PrivacyRedactor()).extract(
        "oi", slots, conversation_id="conv", budget=0.2
    )
    assert result.tokens_esgotados and result.slots == slots
    assert client.requests[0].budget == 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response", ['{"idade":"sensitive@example.com"}', '{"extra":"private"}', "not JSON"]
)
async def test_invalid_response_has_generic_public_error(response: str) -> None:
    client = FakeClient(response)
    with pytest.raises(LLMContractError) as caught:
        await SlotExtractor(client, PrivacyRedactor()).extract(
            "oi", Slots(), conversation_id="conv"
        )
    assert "sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_typed_confirmation_of_transcribed_cep_preserves_value() -> None:
    client = FakeClient("{}", "{}")
    extractor = SlotExtractor(client, PrivacyRedactor())
    uncertain = await extractor.extract(
        "07234567", Slots(), conversation_id="conv", proveniencia="transcrito"
    )
    confirmed = await extractor.extract("CEP 07234567", uncertain.slots, conversation_id="conv")
    assert confirmed.slots.cep is not None
    assert confirmed.slots.cep.valor == "07234567"
    assert confirmed.slots.cep.status == "informado"


@pytest.mark.asyncio
async def test_labeled_money_without_currency_is_redacted() -> None:
    client = FakeClient("{}")
    await SlotExtractor(client, PrivacyRedactor()).extract(
        "prêmio 199,90, franquia: 2500 e pro-rata 39,99", Slots(), conversation_id="conv"
    )
    for amount in ("199,90", "2500", "39,99"):
        assert amount not in client.requests[0].user


@pytest.mark.asyncio
async def test_punctuated_cep_anywhere_and_price_sentence_are_redacted() -> None:
    client = FakeClient("{}")
    await SlotExtractor(client, PrivacyRedactor()).extract(
        "Moro no 07.234-567 e o preço é 199,90", Slots(), conversation_id="conv"
    )
    assert "07.234-567" not in client.requests[0].user
    assert "199,90" not in client.requests[0].user


@pytest.mark.parametrize("value", ["amanhã", "2026-02-30", "20260911", "11/09/2026"])
def test_informed_start_date_requires_valid_iso_date(value: str) -> None:
    with pytest.raises(ValidationError):
        Slots.model_validate({"data_inicio": informed(value)})


def test_start_date_preserves_iso_and_allows_explicit_uncertainty() -> None:
    assert (
        Slots.model_validate({"data_inicio": informed("2026-09-11")}).data_inicio.valor
        == "2026-09-11"
    )
    uncertain = {"valor": "amanhã", "status": "incerto", "proveniencia": "digitado"}
    assert Slots.model_validate({"data_inicio": uncertain}).data_inicio.status == "incerto"


@pytest.mark.asyncio
async def test_token_exhaustion_retains_cep_captured_in_current_message() -> None:
    client = FakeClient(TokenBudgetExceeded())
    result = await SlotExtractor(client, PrivacyRedactor()).extract(
        "CEP 07234567", Slots(), conversation_id="conv"
    )
    assert result.tokens_esgotados
    assert result.slots.cep is not None and result.slots.cep.valor == "07234567"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["transport", "contract", "invalid_json"])
async def test_extraction_errors_expose_preserved_slots_without_pii_in_repr(kind: str) -> None:
    from application.llm import LLMUnavailable

    error = LLMUnavailable() if kind == "transport" else LLMContractError()
    client = FakeClient("invalid JSON" if kind == "invalid_json" else error)
    slots = Slots(idade=SlotValue[int](valor=35, status="informado", proveniencia="digitado"))
    with pytest.raises(LLMUnavailable if kind == "transport" else LLMContractError) as caught:
        await SlotExtractor(client, PrivacyRedactor()).extract(
            "CEP 07234567", slots, conversation_id="conv"
        )
    expected_type = "ExtractionUnavailable" if kind == "transport" else "ExtractionContractError"
    assert type(caught.value).__name__ == expected_type
    assert caught.value.slots.cep.valor == "07234567"
    assert caught.value.slots.idade == slots.idade
    assert "07234567" not in str(caught.value)
    assert "07234567" not in repr(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_fixture_errors_remain_distinguishable_for_fail_fast(missing: bool) -> None:
    from infrastructure.llm.recording import LLMFixtureInvalid, LLMFixtureMissing

    error = LLMFixtureMissing("synthetic-digest") if missing else LLMFixtureInvalid()
    client = FakeClient(error)
    with pytest.raises(type(error)) as caught:
        await SlotExtractor(client, PrivacyRedactor()).extract(
            "oi", Slots(), conversation_id="conv"
        )
    assert caught.value is error


@pytest.mark.asyncio
async def test_uncertain_without_candidate_does_not_erase_collected_year():
    client = FakeClient(json.dumps({
        "idade": informed(80),
        "veiculo_ano": {"valor": None, "status": "incerto", "proveniencia": "digitado"},
    }))
    previous = Slots.model_validate({"veiculo_ano": informed(2003)})
    result = await SlotExtractor(client, PrivacyRedactor()).extract(
        "Tenho 80 anos", previous, conversation_id="conv"
    )
    assert result.slots.veiculo_ano == previous.veiculo_ano
    assert result.slots.idade.valor == 80
