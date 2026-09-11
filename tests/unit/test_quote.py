from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
from decimal import Decimal
from typing import Any

import pytest

from domain.quote import Declined, Quote, QuoteContractError, QuoteOutcome, QuoteUnavailable


@pytest.mark.parametrize(
    "field",
    ["plano_id", "plano_nome", "premio_mensal", "franquia", "coberturas", "carencia", "moeda"],
)
def test_missing_required_field_is_contract_error(
    quote_payload: dict[str, Any], field: str
) -> None:
    payload = deepcopy(quote_payload)
    del payload[field]
    with pytest.raises(QuoteContractError):
        Quote.from_api(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("premio_mensal",), True),
        (("premio_mensal",), "209.90"),
        (("premio_mensal",), float("nan")),
        (("franquia",), float("inf")),
        (("franquia",), -1),
        (("plano_nome",), None),
        (("plano_id",), ""),
        (("coberturas",), "roubo"),
        (("coberturas",), [1]),
        (("carencia",), None),
        (("carencia", "dias"), True),
        (("carencia", "dias"), -1),
        (("carencia", "coberturas"), [None]),
        (("carencia", "observacao"), 3),
        (("primeiro_pagamento_pro_rata",), None),
        (("primeiro_pagamento_pro_rata",), {}),
        (("primeiro_pagamento_pro_rata", "dias_no_mes"), 0),
        (("primeiro_pagamento_pro_rata", "dias_cobrados"), 31),
        (("primeiro_pagamento_pro_rata", "valor_primeiro_pagamento"), "invalido"),
    ],
)
def test_malformed_response_is_contract_error(
    quote_payload: dict[str, Any], path: tuple[str, ...], value: object
) -> None:
    payload = deepcopy(quote_payload)
    target = payload if len(path) == 1 else payload[path[0]]
    target[path[-1]] = value
    with pytest.raises(QuoteContractError):
        Quote.from_api(payload)


@pytest.mark.parametrize("payload", [None, [], "01310-100", 1])
def test_non_object_response_is_contract_error(payload: object) -> None:
    with pytest.raises(QuoteContractError):
        Quote.from_api(payload)


def test_declined_is_result_and_errors_are_distinct() -> None:
    outcome: QuoteOutcome = Declined(motivo="Perfil fora da aceitação")
    assert isinstance(outcome, Declined)
    assert not isinstance(outcome, BaseException)
    assert not issubclass(QuoteContractError, QuoteUnavailable)
    assert not issubclass(QuoteUnavailable, QuoteContractError)


def test_parse_preserves_api_facts_and_decimal_money(quote_payload: dict[str, Any]) -> None:
    quote = Quote.from_api(quote_payload)
    assert quote.plano_id == quote_payload["plano_id"]
    assert quote.plano_nome == quote_payload["plano_nome"]
    assert quote.premio_mensal == Decimal("209.9")
    assert quote.franquia == Decimal("3000")
    assert isinstance(quote.premio_mensal, Decimal)
    assert isinstance(quote.franquia, Decimal)
    assert quote.coberturas == tuple(quote_payload["coberturas"])
    assert quote.moeda == quote_payload["moeda"]
    assert quote.carencia.dias == quote_payload["carencia"]["dias"]
    assert quote.carencia.observacao == quote_payload["carencia"]["observacao"]
    assert quote.carencia.coberturas == tuple(quote_payload["carencia"]["coberturas"])
    pro_rata = quote.primeiro_pagamento_pro_rata
    assert pro_rata is not None
    assert pro_rata.valor_primeiro_pagamento == Decimal("111.95")
    assert isinstance(pro_rata.valor_primeiro_pagamento, Decimal)
    assert (pro_rata.dias_no_mes, pro_rata.dias_cobrados) == (30, 16)


def test_absent_pro_rata_is_none_not_zero(quote_payload: dict[str, Any]) -> None:
    payload = deepcopy(quote_payload)
    del payload["primeiro_pagamento_pro_rata"]
    assert Quote.from_api(payload).primeiro_pagamento_pro_rata is None


def test_zero_pro_rata_still_applies(quote_payload: dict[str, Any]) -> None:
    payload = deepcopy(quote_payload)
    payload["primeiro_pagamento_pro_rata"]["valor_primeiro_pagamento"] = 0
    pro_rata = Quote.from_api(payload).primeiro_pagamento_pro_rata
    assert pro_rata is not None
    assert pro_rata.valor_primeiro_pagamento == Decimal(0)


def test_quote_detaches_from_payload_and_discards_pricing(quote_payload: dict[str, Any]) -> None:
    payload = deepcopy(quote_payload)
    quote = Quote.from_api(payload)
    payload["coberturas"].clear()
    payload["carencia"]["coberturas"].clear()
    assert quote.coberturas == tuple(quote_payload["coberturas"])
    assert quote.carencia.coberturas == tuple(quote_payload["carencia"]["coberturas"])
    assert "multiplicadores" not in asdict(quote)
    assert not hasattr(quote, "__dict__")
    with pytest.raises(FrozenInstanceError):
        quote.franquia = Decimal(0)  # type: ignore[misc]


def test_contract_error_does_not_echo_input(quote_payload: dict[str, Any]) -> None:
    payload = deepcopy(quote_payload)
    payload["premio_mensal"] = "01310-100"
    with pytest.raises(QuoteContractError) as caught:
        Quote.from_api(payload)
    assert "01310-100" not in str(caught.value)
