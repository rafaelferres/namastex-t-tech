"""Disciplina de erro nos clientes externos: status classificado, corpo preservado e redigido."""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from application.external import ConfigurationError, StartupCheckError
from application.llm import (
    LLMConfigurationError,
    LLMContractError,
    LLMRequest,
    LLMRole,
    LLMUnavailable,
)
from application.outbox import (
    HandoffConfigurationError,
    HandoffDeliveryError,
    HandoffDispatcher,
    HandoffEffect,
)
from application.startup import verify_dependencies
from application.tracing import Correlation
from domain.handoff import ConversationContext, HandoffPolicy
from domain.quote import (
    QuoteConfigurationError,
    QuoteContractError,
    QuoteRequest,
    QuoteUnavailable,
)
from infrastructure.handoff import WebhookHandoffSink
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.planos.client import PlanosClient, PlanosConfigurationError, PlanosUnavailable
from infrastructure.quote.guard import EligibilityGuardProvider
from infrastructure.quote.http import HttpQuoteProvider
from infrastructure.quote.trace import WireTrace
from infrastructure.tracing.correlation import ContextCorrelationProvider
from tests.fakes import FakeClock

REJECTED = (
    '{"error":{"message":"No endpoints found that can handle the requested parameters.",'
    '"code":404}}'
)


def transport(status: int, body: str = REJECTED) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(status, text=body))


async def call_llm(status: int, body: str = REJECTED) -> object:
    request = LLMRequest("c", LLMRole.CONVERSATION, "sistema", "usuário", {"type": "object"}, 5.0)
    async with httpx.AsyncClient(transport=transport(status, body)) as http:
        return await OpenRouterLLMClient(http, LLMConfig(api_key="k"), FakeClock()).complete(
            request
        )


async def read_planos(status: int, body: str = REJECTED) -> object:
    async with httpx.AsyncClient(
        transport=transport(status, body), base_url="https://quote.test"
    ) as http:
        return await PlanosClient(http, clock=FakeClock(), ttl=0, timeout=2.0).current()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_llm_configuration_rejection_is_not_contract_error_and_keeps_body(status, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(LLMConfigurationError) as caught:
        await call_llm(status)
    assert not isinstance(caught.value, LLMContractError)
    assert isinstance(caught.value, ConfigurationError)
    assert f"HTTP {status}" in caught.value.detalhe
    assert "No endpoints found" in caught.value.detalhe
    assert "No endpoints found" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_llm_transient_status_is_unavailable_with_body(status, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(LLMUnavailable) as caught:
        await call_llm(status, '{"error":"rate limited"}')
    assert "rate limited" in (caught.value.detalhe or "")
    assert "rate limited" in caplog.text


@pytest.mark.asyncio
async def test_llm_invalid_success_body_is_contract_error_with_body(caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(LLMContractError) as caught:
        await call_llm(200, '{"inesperado": true}')
    assert "inesperado" in (caught.value.detalhe or "")
    assert "inesperado" in caplog.text


@pytest.mark.asyncio
async def test_failure_body_is_redacted_before_exception_and_log(caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(LLMUnavailable) as caught:
        await call_llm(503, "cliente 529.982.247-25 no CEP 01310-100")
    for secret in ("529.982.247-25", "01310-100"):
        assert secret not in (caught.value.detalhe or "")
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_planos_configuration_error_is_loud_not_fail_open(status, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(PlanosConfigurationError) as caught:
        await read_planos(status)
    assert not isinstance(caught.value, PlanosUnavailable)
    assert "No endpoints found" in caught.value.detalhe
    assert "No endpoints found" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_planos_transient_failure_keeps_fail_open_and_logs_body(status, caplog):
    with caplog.at_level(logging.WARNING):
        assert await read_planos(status, '{"error":"upstream_unavailable"}') is None
    assert "upstream_unavailable" in caplog.text


@pytest.mark.asyncio
async def test_guard_does_not_swallow_configuration_error():
    rules = AsyncMock(
        current=AsyncMock(side_effect=PlanosConfigurationError("planos", "HTTP 404: rota"))
    )
    inner = AsyncMock()
    guard = EligibilityGuardProvider(inner, rules, FakeClock())
    with pytest.raises(PlanosConfigurationError):
        await guard.quote(QuoteRequest("completo", 30, 2020))
    inner.quote.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_quote_configuration_rejection_is_distinguishable_and_redacted(status):
    async with httpx.AsyncClient(
        transport=transport(status, "rota inexistente para 01310-100"),
        base_url="https://quote.test",
    ) as http:
        provider = HttpQuoteProvider(http, timeout=2.0, clock=FakeClock())
        with pytest.raises(QuoteConfigurationError) as caught:
            await provider.quote(QuoteRequest("completo", 30, 2020))
    assert isinstance(caught.value, QuoteContractError)  # continua sem retry
    assert "rota inexistente" in (caught.value.detalhe or "")
    assert "01310-100" not in (caught.value.detalhe or "")


@pytest.mark.asyncio
async def test_quote_failure_body_reaches_the_trace():
    recorded = []
    recorder = Mock(record=recorded.append)
    correlation = ContextCorrelationProvider(lambda: Correlation("t", "c"))
    async with httpx.AsyncClient(
        transport=transport(503, '{"error":"upstream_unavailable"}'),
        base_url="https://quote.test",
    ) as http:
        leaf = HttpQuoteProvider(
            http, timeout=2.0, clock=FakeClock(), observe_status=correlation.observe_http
        )
        provider = WireTrace(leaf, recorder, correlation, FakeClock())
        with pytest.raises(QuoteUnavailable):
            await provider.quote(QuoteRequest("completo", 30, 2020))
    assert "HTTP 503" in recorded[0].erro
    assert "upstream_unavailable" in recorded[0].erro


def _decision():
    return HandoffPolicy().evaluate(ConversationContext(pede_humano=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_sink_configuration_rejection_is_distinguishable_with_body(status):
    async with httpx.AsyncClient(transport=transport(status, "rota inexistente")) as http:
        with pytest.raises(HandoffConfigurationError) as caught:
            await WebhookHandoffSink(http, "https://sales.test/x").emit(
                _decision(), conversation_id="c", idempotency_key="k"
            )
    assert "rota inexistente" in caught.value.detalhe


@pytest.mark.asyncio
async def test_sink_transient_failure_keeps_status_and_body():
    async with httpx.AsyncClient(transport=transport(503, "fila fora do ar")) as http:
        with pytest.raises(HandoffDeliveryError) as caught:
            await WebhookHandoffSink(http, "https://sales.test/x").emit(
                _decision(), conversation_id="c", idempotency_key="k"
            )
    assert not isinstance(caught.value, ConfigurationError)
    assert "HTTP 503" in (caught.value.detalhe or "")
    assert "fila fora do ar" in (caught.value.detalhe or "")


@pytest.mark.asyncio
async def test_dispatcher_persists_failure_detail_not_just_class_name():
    effect = HandoffEffect("h1:webhook_vendas", "c", "webhook_vendas", _decision(), 0)
    outbox = AsyncMock(due_handoffs=AsyncMock(return_value=(effect,)))
    failing = AsyncMock(
        emit=AsyncMock(side_effect=HandoffConfigurationError("webhook_vendas", "HTTP 404: rota"))
    )
    sinks = {"lead": AsyncMock(), "webhook_vendas": failing, "api_fila": AsyncMock()}
    clock = FakeClock(instant=datetime(2026, 9, 11, 12))
    await HandoffDispatcher(outbox, sinks, clock=clock).drain_due()
    error = outbox.mark_handoff_failed.call_args.kwargs["error"]
    assert "HandoffConfigurationError" in error
    assert "HTTP 404: rota" in error


@pytest.mark.asyncio
async def test_startup_check_fails_loudly_on_invalid_credential_and_rejected_parameter():
    with pytest.raises(StartupCheckError) as caught:
        await verify_dependencies(
            {
                "llm_extrator": lambda: call_llm(401, '{"error":"invalid api key"}'),
                "llm_conversador": lambda: call_llm(404),
            }
        )
    message = str(caught.value)
    assert "llm_extrator" in message and "invalid api key" in message
    assert "llm_conversador" in message and "No endpoints found" in message


@pytest.mark.asyncio
async def test_startup_check_tolerates_transient_failure(caplog):
    with caplog.at_level(logging.WARNING):
        report = await verify_dependencies(
            {"llm_extrator": lambda: call_llm(503, "sobrecarga"), "ok": AsyncMock()}
        )
    assert report == {"llm_extrator": "indisponivel", "ok": "ok"}
    assert "sobrecarga" in caplog.text
