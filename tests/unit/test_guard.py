from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from domain.acceptance import AcceptanceRules
from domain.quote import Declined, QuoteRequest
from infrastructure.quote.guard import EligibilityGuardProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["idade", "veiculo", "plano"])
async def test_guard_declines_without_inner(plans_payload: dict[str, Any], reason: str) -> None:
    rules = AcceptanceRules.from_api(plans_payload)
    age = max(f.maximo for f in rules.faixas_idade if f.motivo_recusa is None)
    years = max(f.maximo for f in rules.faixas_veiculo if f.motivo_recusa is None)
    req = QuoteRequest(sorted(rules.planos_validos)[0], age, 2026)
    req = replace(
        req,
        **{
            "idade": {"idade": age + 1}.get(reason, age),
            "veiculo_ano": 2026 - years - 1 if reason == "veiculo" else 2026,
            "plano_id": "inexistente" if reason == "plano" else req.plano_id,
        },
    )
    inner = AsyncMock()
    provider = EligibilityGuardProvider(
        inner,
        AsyncMock(current=AsyncMock(return_value=rules)),
        Mock(today=Mock(return_value=date(2026, 9, 11))),
    )
    outcome = await provider.quote(req)
    assert isinstance(outcome, Declined)
    assert outcome.origem == "regra_local"
    inner.quote.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("future", [0, 1, 2])
async def test_guard_preserves_eligible_request(plans_payload: dict[str, Any], future: int) -> None:
    rules = AcceptanceRules.from_api(plans_payload)
    age = min(f.minimo for f in rules.faixas_idade if f.motivo_recusa is None)
    req = QuoteRequest(sorted(rules.planos_validos)[0], age, 2026 + future, "01310100")
    inner = AsyncMock()
    provider = EligibilityGuardProvider(
        inner,
        AsyncMock(current=AsyncMock(return_value=rules)),
        Mock(today=Mock(return_value=date(2026, 9, 11))),
    )
    assert await provider.quote(req) is inner.quote.return_value
    inner.quote.assert_awaited_once_with(req)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, RuntimeError("PII 01310100")])
async def test_guard_fails_open(error: Exception | None, caplog: pytest.LogCaptureFixture) -> None:
    rules = AsyncMock(current=AsyncMock(return_value=None, side_effect=error))
    inner = AsyncMock()
    provider = EligibilityGuardProvider(inner, rules, Mock())
    req = QuoteRequest("completo", 30, 2026)
    assert await provider.quote(req) is inner.quote.return_value
    assert "01310100" not in caplog.text
    if error:
        assert "rules_unavailable" in caplog.text
