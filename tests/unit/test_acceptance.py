from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date
from typing import Any

import pytest

from domain.acceptance import AcceptanceRules
from domain.quote import Declined, QuoteContractError, QuoteRequest


def valid_request(plans: dict[str, Any], **changes: Any) -> QuoteRequest:
    slots = {
        "plano_id": plans["planos"][0]["id"],
        "idade": plans["regras"]["faixa_etaria"][0]["idade_min"],
        "veiculo_ano": 2026,
    }
    return QuoteRequest(**(slots | changes))


@pytest.mark.parametrize(
    ("boundary", "offset", "declined"),
    [
        ("min", -1, True),
        ("min", 0, False),
        ("max", 0, False),
        ("max", 1, True),
    ],
)
def test_age_boundaries_from_real_fixture(
    plans_payload: dict[str, Any], boundary: str, offset: int, declined: bool
) -> None:
    accepted = [f for f in plans_payload["regras"]["faixa_etaria"] if not f.get("recusar")]
    limit = (
        min(f["idade_min"] for f in accepted)
        if boundary == "min"
        else max(f["idade_max"] for f in accepted)
    )
    outcome = AcceptanceRules.from_api(plans_payload).evaluate(
        valid_request(plans_payload, idade=limit + offset), date(2026, 9, 11)
    )
    assert isinstance(outcome, Declined) if declined else outcome is None


@pytest.mark.parametrize(("offset", "declined"), [(0, False), (1, True)])
def test_vehicle_boundary_from_fixture(
    plans_payload: dict[str, Any], offset: int, declined: bool
) -> None:
    accepted = [f for f in plans_payload["regras"]["idade_veiculo"] if not f.get("recusar")]
    limit = max(f["anos_max"] for f in accepted)
    outcome = AcceptanceRules.from_api(plans_payload).evaluate(
        valid_request(plans_payload, veiculo_ano=2026 - limit - offset), date(2026, 9, 11)
    )
    assert isinstance(outcome, Declined) if declined else outcome is None


def test_next_model_year_is_accepted(plans_payload: dict[str, Any]) -> None:
    assert (
        AcceptanceRules.from_api(plans_payload).evaluate(
            valid_request(plans_payload, veiculo_ano=2027), date(2026, 9, 11)
        )
        is None
    )


def test_unknown_plan_is_declined_without_echoing_input(plans_payload: dict[str, Any]) -> None:
    result = AcceptanceRules.from_api(plans_payload).evaluate(
        valid_request(plans_payload, plano_id="01310-100"), date(2026, 9, 11)
    )
    assert isinstance(result, Declined)
    assert result.motivo
    assert "01310-100" not in result.motivo


def test_rules_follow_changed_fixture_and_keep_decline_reason(
    plans_payload: dict[str, Any],
) -> None:
    payload = deepcopy(plans_payload)
    age_bands = payload["regras"]["faixa_etaria"]
    formerly_accepted = age_bands[0]["idade_min"]
    age_bands[0]["idade_min"] += 1
    age_bands[1]["recusar"] = True
    age_bands[1]["motivo"] = "Faixa suspensa pela seguradora"
    vehicle_bands = payload["regras"]["idade_veiculo"]
    previous_vehicle_limit = vehicle_bands[2]["anos_max"]
    vehicle_bands[2]["anos_max"] -= 1
    payload["planos"][0]["id"] = "novo_plano"
    rules = AcceptanceRules.from_api(payload)
    today = date(2026, 9, 11)
    assert isinstance(
        rules.evaluate(valid_request(payload, idade=formerly_accepted), today), Declined
    )
    refused = rules.evaluate(valid_request(payload, idade=age_bands[1]["idade_min"]), today)
    assert isinstance(refused, Declined)
    assert refused.motivo == age_bands[1]["motivo"]
    assert isinstance(
        rules.evaluate(
            valid_request(payload, veiculo_ano=today.year - previous_vehicle_limit), today
        ),
        Declined,
    )
    assert isinstance(rules.evaluate(valid_request(plans_payload), today), Declined)
    assert rules.evaluate(valid_request(payload), today) is None


def test_evaluation_uses_injected_year(plans_payload: dict[str, Any]) -> None:
    rules = AcceptanceRules.from_api(plans_payload)
    limit = max(
        f["anos_max"] for f in plans_payload["regras"]["idade_veiculo"] if not f.get("recusar")
    )
    req = valid_request(plans_payload, veiculo_ano=2026 - limit)
    assert rules.evaluate(req, date(2026, 12, 31)) is None
    assert isinstance(rules.evaluate(req, date(2027, 1, 1)), Declined)


def test_acceptance_projection_does_not_retain_pricing_or_source(
    plans_payload: dict[str, Any],
) -> None:
    payload = deepcopy(plans_payload)
    rules = AcceptanceRules.from_api(payload)
    assert not hasattr(rules, "__dict__")
    representation = repr(asdict(rules))
    for forbidden in ["base_mensal", "multiplicador", "regiao_cep", "franquia"]:
        assert forbidden not in representation
    payload["planos"].clear()
    payload["regras"].clear()
    assert rules.evaluate(valid_request(plans_payload), date(2026, 9, 11)) is None


def test_profile_is_judged_only_on_the_dimensions_given(plans_payload: dict[str, Any]) -> None:
    rules = AcceptanceRules.from_api(plans_payload)
    today = date(2026, 9, 11)
    assert rules.evaluate_profile(idade=None, veiculo_ano=None, hoje=today) is None
    assert rules.evaluate_profile(idade=30, veiculo_ano=None, hoje=today) is None
    assert isinstance(rules.evaluate_profile(idade=80, veiculo_ano=None, hoje=today), Declined)
    assert isinstance(rules.evaluate_profile(idade=None, veiculo_ano=1990, hoje=today), Declined)
    both = valid_request(plans_payload, idade=80, veiculo_ano=1990)
    assert rules.evaluate_profile(idade=80, veiculo_ano=1990, hoje=today) == rules.evaluate(
        both, today
    )


@pytest.mark.parametrize("payload", [None, {}, {"planos": [], "regras": {}}, {"regras": []}])
def test_malformed_rules_are_contract_error(payload: object) -> None:
    with pytest.raises(QuoteContractError):
        AcceptanceRules.from_api(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("idade_min", True),
        ("idade_min", -1),
        ("idade_min", 1000),
        ("recusar", "false"),
    ],
)
def test_malformed_band_is_contract_error(
    plans_payload: dict[str, Any], field: str, value: object
) -> None:
    payload = deepcopy(plans_payload)
    payload["regras"]["faixa_etaria"][0][field] = value
    with pytest.raises(QuoteContractError):
        AcceptanceRules.from_api(payload)
