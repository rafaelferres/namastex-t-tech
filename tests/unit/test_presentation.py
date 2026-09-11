from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest

from agent.templates import (
    format_brl,
    render_declined,
    render_handoff,
    render_quote,
    render_unavailable,
)
from domain.acceptance import AcceptanceRules
from domain.quote import Declined, Quote, QuoteContractError, QuoteRequest
from infrastructure.planos.projections import project_planos

FIXTURES = Path(__file__).parents[1] / "fixtures/presentation"
GOLDENS = Path(__file__).parents[1] / "golden_files/presentation"


@pytest.mark.parametrize(
    "amount, expected",
    [
        ("313.80", "R$ 313,80"),
        ("1234.56", "R$ 1.234,56"),
        ("12345678901234567890.10", "R$ 12.345.678.901.234.567.890,10"),
    ],
)
def test_money_format_is_exact(amount: str, expected: str) -> None:
    with localcontext() as ctx:
        ctx.prec = 3
        assert format_brl(Decimal(amount)) == expected


def test_fractional_cent_is_not_silently_rounded() -> None:
    with pytest.raises(QuoteContractError):
        format_brl(Decimal("313.801"))


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_real_plan_payload_matches_golden(fixture: Path, plans_payload: dict[str, Any]) -> None:
    payload = json.loads(fixture.read_text(), parse_float=Decimal)
    facts = next(
        f for f in project_planos(plans_payload).product_facts if f.plano_id == payload["plano_id"]
    )
    text = render_quote(payload, facts)
    assert text + "\n" == (GOLDENS / f"{fixture.stem}.txt").read_text()
    assert "Carência de 30 dias para roubo e furto" in text
    assert all(word not in text.casefold() for word in ("multiplicador", "base_mensal", "fórmula"))
    if "primeiro_pagamento_pro_rata" not in payload:
        assert "pro-rata" not in text and "primeiro mês" not in text and "R$ 0,00" not in text
    else:
        assert "pro-rata" in text


def test_waiting_period_comes_from_payload(
    plans_payload: dict[str, Any], quote_payload: dict[str, Any]
) -> None:
    quote = Quote.from_api(quote_payload)
    quote = replace(quote, carencia=replace(quote.carencia, dias=45))
    facts = next(
        f for f in project_planos(plans_payload).product_facts if f.plano_id == quote.plano_id
    )
    assert "Carência de 45 dias" in render_quote(quote, facts)


@pytest.mark.parametrize("reason", ["idade", "veiculo", "plano"])
def test_decline_is_specific_and_final(reason: str, plans_payload: dict[str, Any]) -> None:
    from datetime import date

    rules = AcceptanceRules.from_api(plans_payload)
    max_age = max(f.maximo for f in rules.faixas_idade if f.motivo_recusa is None)
    max_years = max(f.maximo for f in rules.faixas_veiculo if f.motivo_recusa is None)
    req = QuoteRequest(
        "missing" if reason == "plano" else "completo",
        max_age + 1 if reason == "idade" else max_age,
        2026 - max_years - 1 if reason == "veiculo" else 2026,
    )
    declined = rules.evaluate(req, date(2026, 9, 11))
    assert isinstance(declined, Declined)
    text = render_declined(declined)
    assert declined.motivo in text
    assert all(
        word not in text.lower() for word in ("retorno", "aguarde", "verificar", "atendente")
    )


def test_unavailable_and_handoff_do_not_invent_prices_or_deadlines() -> None:
    for text in (render_unavailable(), render_handoff()):
        assert "R$" not in text
        assert not any(c.isdigit() for c in text)
