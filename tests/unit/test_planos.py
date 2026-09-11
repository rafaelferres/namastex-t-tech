from __future__ import annotations

import traceback
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
from datetime import date, datetime
from typing import Any

import httpx
import pytest

from application.ports import AcceptanceRulesProvider
from domain.acceptance import AcceptanceRules
from domain.quote import QuoteContractError, QuoteRequest
from infrastructure.planos.client import PlanosClient, PlanosConfigurationError, PlanosUnavailable
from infrastructure.planos.projections import project_planos
from tests.fakes import FakeClock


def test_real_catalog_projects_acceptance_and_only_safe_product_facts(
    plans_payload: dict[str, Any],
) -> None:
    catalog = project_planos(plans_payload)
    assert catalog.acceptance_rules == AcceptanceRules.from_api(plans_payload)
    assert len(catalog.product_facts) == len(plans_payload["planos"])
    for facts, original in zip(catalog.product_facts, plans_payload["planos"], strict=True):
        assert facts.plano_id == original["id"]
        assert facts.nome == original["nome"]
        assert facts.coberturas == tuple(original["coberturas"])
        assert facts.tem_carencia is True
        assert not hasattr(facts, "__dict__")
    serialized = repr(asdict(catalog))
    for forbidden in ["base_mensal", "multiplicador", "franquia", "regiao_cep"]:
        assert forbidden not in serialized


@pytest.mark.parametrize("change", ["no_wait", "no_covered_risk"])
def test_waiting_period_existence_depends_on_applicable_coverages(
    plans_payload: dict[str, Any], change: str
) -> None:
    payload = deepcopy(plans_payload)
    if change == "no_wait":
        payload["regras"]["carencia"]["dias"] = 0
    else:
        payload["regras"]["carencia"]["coberturas_com_carencia"] = ["risco_nao_coberto"]
    assert all(not facts.tem_carencia for facts in project_planos(payload).product_facts)


def test_projections_are_immutable_and_detached_from_original_payload(
    plans_payload: dict[str, Any],
) -> None:
    payload = deepcopy(plans_payload)
    catalog = project_planos(payload)
    payload["planos"][0]["coberturas"].clear()
    payload["regras"].clear()
    assert catalog.product_facts[0].coberturas == tuple(plans_payload["planos"][0]["coberturas"])
    assert (
        catalog.acceptance_rules.evaluate(QuoteRequest("completo", 30, 2026), date(2026, 9, 11))
        is None
    )
    with pytest.raises(FrozenInstanceError):
        catalog.product_facts[0].nome = "Outro"  # type: ignore[misc]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_name",
        "invalid_coverages",
        "invalid_wait",
        "missing_rules",
        "empty_catalog",
        "duplicate_id",
    ],
)
def test_malformed_catalog_is_contract_error(plans_payload: dict[str, Any], mutation: str) -> None:
    payload = deepcopy(plans_payload)
    if mutation == "missing_name":
        del payload["planos"][0]["nome"]
    elif mutation == "invalid_coverages":
        payload["planos"][0]["coberturas"] = "roubo"
    elif mutation == "invalid_wait":
        payload["regras"]["carencia"]["dias"] = True
    elif mutation == "missing_rules":
        del payload["regras"]["faixa_etaria"]
    elif mutation == "empty_catalog":
        payload["planos"] = []
    else:
        payload["planos"].append(deepcopy(payload["planos"][0]))
    with pytest.raises(QuoteContractError):
        project_planos(payload)


@pytest.mark.asyncio
async def test_client_fetches_real_fixture_and_shares_cache_between_projections(
    plans_payload: dict[str, Any],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        assert request.url.path == "/planos"
        assert request.extensions["timeout"] == dict(connect=1.5, read=1.5, write=1.5, pool=1.5)
        return httpx.Response(200, json=plans_payload)

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        catalog_client = PlanosClient(client, clock=FakeClock(), ttl=60, timeout=1.5)
        provider: AcceptanceRulesProvider = catalog_client
        catalog = await catalog_client.get()
        assert await provider.current() == catalog.acceptance_rules
        assert await catalog_client.get() is catalog
        assert not client.is_closed
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_expires_at_monotonic_deadline_not_calendar_change(
    plans_payload: dict[str, Any],
) -> None:
    calls = 0
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = deepcopy(plans_payload)
        if calls > 1:
            payload["planos"][0]["nome"] = "Nome atualizado"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        catalog_client = PlanosClient(client, clock=clock, ttl=10, timeout=1)
        first = await catalog_client.get()
        clock.elapsed = 9.999
        clock.instant = datetime(2027, 1, 1)
        assert await catalog_client.get() is first
        assert calls == 1
        clock.elapsed = 10
        renewed = await catalog_client.get()
        assert renewed.product_facts[0].nome == "Nome atualizado"
        assert calls == 2
        clock.elapsed = 19.999
        assert await catalog_client.get() is renewed
        assert calls == 2


@pytest.mark.asyncio
async def test_ttl_starts_after_successful_fetch(plans_payload: dict[str, Any]) -> None:
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        clock.elapsed += 30
        return httpx.Response(200, json=plans_payload)

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        catalog_client = PlanosClient(client, clock=clock, ttl=10, timeout=1)
        first = await catalog_client.get()
        clock.elapsed = 39.999
        assert await catalog_client.get() is first
        assert calls == 1
        clock.elapsed = 40
        await catalog_client.get()
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "http", "redirect", "json", "projection"])
async def test_failed_refresh_is_not_retried_or_cached_or_replaced_by_stale_data(
    plans_payload: dict[str, Any], failure: str
) -> None:
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls != 2:
            return httpx.Response(200, json=plans_payload)
        if failure == "transport":
            raise httpx.ConnectError("CEP 01310100", request=request)
        if failure == "http":
            return httpx.Response(503, text="CEP 01310100")
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "/redirect"})
        if failure == "json":
            return httpx.Response(200, text="CEP 01310100")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        catalog_client = PlanosClient(client, clock=clock, ttl=10, timeout=1)
        await catalog_client.get()
        clock.elapsed = 10
        error = (
            QuoteContractError
            if failure in ("json", "projection")
            # Redirecionamento é URL base errada: configuração, nunca fail-open (D-035).
            else PlanosConfigurationError
            if failure == "redirect"
            else PlanosUnavailable
        )
        with pytest.raises(error) as caught:
            await catalog_client.get()
        assert "01310100" not in "".join(traceback.format_exception(caught.value))
        assert calls == 2
        assert (await catalog_client.get()).acceptance_rules == AcceptanceRules.from_api(
            plans_payload
        )
        assert calls == 3


@pytest.mark.asyncio
async def test_unavailable_rules_can_fail_open_without_expired_rules() -> None:
    async with httpx.AsyncClient(
        base_url="https://quote.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    ) as client:
        assert await PlanosClient(client, clock=FakeClock(), ttl=10, timeout=1).current() is None


@pytest.mark.asyncio
async def test_zero_ttl_disables_reuse(plans_payload: dict[str, Any]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=plans_payload)

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        catalog_client = PlanosClient(client, clock=FakeClock(), ttl=0, timeout=1)
        await catalog_client.get()
        await catalog_client.get()
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [-1, float("nan"), float("inf")])
async def test_invalid_ttl_is_rejected(ttl: float) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        with pytest.raises(ValueError):
            PlanosClient(client, clock=FakeClock(), ttl=ttl, timeout=1)


def test_pricing_fields_are_not_required_to_build_projections(
    plans_payload: dict[str, Any],
) -> None:
    payload = deepcopy(plans_payload)
    for plano in payload["planos"]:
        del plano["base_mensal"]
        del plano["franquia"]
    for name in ["faixa_etaria", "idade_veiculo"]:
        for band in payload["regras"][name]:
            band.pop("multiplicador", None)
    del payload["regras"]["regiao_cep"]
    assert project_planos(payload) == project_planos(plans_payload)
