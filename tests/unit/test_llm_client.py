from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from application.llm import LLMRequest, LLMRole
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from tests.virtual_time import virtual_time


def request(role: LLMRole = LLMRole.EXTRACTOR, budget: float = 2.5) -> LLMRequest:
    return LLMRequest(
        "conversation-1",
        role,
        "Extraia slots.",
        "Tenho 30 anos.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        budget,
    )


def test_structured_request_uses_role_model_and_preserves_usage_decimal() -> None:
    with virtual_time() as timeline:

        async def handle(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
            assert req.headers["authorization"] == "Bearer secret"
            assert body["model"] == "openai/gpt-4.1-mini"
            assert body["response_format"]["json_schema"]["strict"] is True
            assert body["response_format"]["json_schema"]["schema"] == request().schema
            assert body["provider"] == {"require_parameters": True}
            assert body["messages"] == [
                {"role": "system", "content": "Extraia slots."},
                {"role": "user", "content": "Tenho 30 anos."},
            ]
            assert req.extensions["timeout"]["read"] == 2.0
            await timeline.sleep(0.25)
            return httpx.Response(
                200,
                content='{"model":"openai/gpt-4.1-mini",'
                '"choices":[{"message":{"content":"{}"}}],'
                '"usage":{"prompt_tokens":17,"completion_tokens":4,"cost":0.000000123456789}}',
            )

        async def run() -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
                config = LLMConfig(api_key="secret", timeout_seconds=2.0, budget_seconds=2.5)
                result = await OpenRouterLLMClient(transport, config, timeline).complete(
                    request()
                )
            assert result.content == "{}"
            assert result.prompt_tokens == 17
            assert result.completion_tokens == 4
            assert result.cost == Decimal("0.000000123456789")
            assert result.latency_ms == 250

        timeline.run(run())


def test_config_reads_environment_and_hides_secret() -> None:
    config = LLMConfig.from_env(
        {
            "OPENROUTER_API_KEY": "private-secret",
            "LLM_EXTRACTOR_MODEL": "vendor/small",
            "LLM_CONVERSATION_MODEL": "vendor/large",
            "LLM_TIMEOUT_SECONDS": "1.2",
            "LLM_BUDGET_SECONDS": "1.8",
            "LLM_CONVERSATION_TOKEN_LIMIT": "123",
        }
    )
    assert "private-secret" not in repr(config)
    assert config.model_for(LLMRole.CONVERSATION) == "vendor/large"
    assert config.timeout_seconds == 1.2
    assert config.budget_seconds == 1.8
    assert config.conversation_token_limit == 123
    with pytest.raises(ValueError):
        LLMConfig(api_key="x", extractor_model="same", conversation_model="same")


@pytest.mark.parametrize(
    "status,error_name",
    [
        (429, "LLMUnavailable"),
        (500, "LLMUnavailable"),
        (408, "LLMUnavailable"),
        (400, "LLMContractError"),
        (401, "LLMContractError"),
        (302, "LLMContractError"),
    ],
)
def test_http_errors_are_generic_and_never_retried(status: int, error_name: str) -> None:
    from application import llm

    calls = []

    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(status, text="private@example.com")

    with virtual_time() as timeline:

        async def run() -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
                with pytest.raises(getattr(llm, error_name)) as caught:
                    await OpenRouterLLMClient(
                        transport, LLMConfig(api_key="secret"), timeline
                    ).complete(request())
                assert "private" not in str(caught.value)

        timeline.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "{}",
        '{"choices":[]}',
        '{"model":"m","choices":[{"message":{"content":null}}],"usage":{}}',
        '{"model":"m","choices":[{"message":{"content":"{}"}}],'
        '"usage":{"prompt_tokens":true,"completion_tokens":2}}',
    ],
)
def test_invalid_envelopes_raise_safe_contract_error(body: str) -> None:
    from application.llm import LLMContractError

    with virtual_time() as timeline:

        async def run() -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))
            ) as transport:
                with pytest.raises(LLMContractError):
                    await OpenRouterLLMClient(
                        transport, LLMConfig(api_key="secret"), timeline
                    ).complete(request())

        timeline.run(run())


@pytest.mark.parametrize("budget", [0.5, 2.5])
def test_overall_deadline_cancels_transport_without_real_sleep(budget: float) -> None:
    from application.llm import LLMUnavailable

    with virtual_time() as timeline:

        async def handle(req: httpx.Request) -> httpx.Response:
            assert req.extensions["timeout"]["read"] == min(2.0, budget)
            await timeline.sleep(20)
            raise AssertionError("deveria cancelar")

        async def run() -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
                with pytest.raises(LLMUnavailable):
                    await OpenRouterLLMClient(
                        transport,
                        LLMConfig(api_key="secret", timeout_seconds=2.0, budget_seconds=2.5),
                        timeline,
                    ).complete(request(budget=budget))

        timeline.run(run())
        assert timeline.monotonic() == budget
        assert timeline.cancelled_sleeps == [20]


def test_transport_exception_is_suppressed() -> None:
    from application.llm import LLMUnavailable

    with virtual_time() as timeline:

        def handle(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("private@example.com", request=req)

        async def run() -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
                with pytest.raises(LLMUnavailable) as caught:
                    await OpenRouterLLMClient(
                        transport, LLMConfig(api_key="secret"), timeline
                    ).complete(request())
                assert caught.value.__suppress_context__
                assert "private" not in str(caught.value)

        timeline.run(run())


@pytest.mark.asyncio
async def test_token_budget_shared_across_roles_records_crossing_and_blocks_next() -> None:
    from application.llm import LLMResponse, TokenBudgetExceeded
    from infrastructure.llm.budget import BudgetedLLMClient

    class Inner:
        calls = 0

        async def complete(self, req: LLMRequest) -> LLMResponse:
            self.calls += 1
            return LLMResponse("{}", "model", 3, 2, None, 0)

    inner = Inner()
    client = BudgetedLLMClient(inner, token_limit=8)
    await client.complete(request())
    with pytest.raises(TokenBudgetExceeded):
        await client.complete(request(LLMRole.CONVERSATION))
    assert client.tokens_used("conversation-1") == 10
    with pytest.raises(TokenBudgetExceeded):
        await client.complete(request())
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_concurrent_budget_check_prevents_second_inference_at_limit() -> None:
    import asyncio
    from dataclasses import replace

    from application.llm import LLMResponse, TokenBudgetExceeded
    from infrastructure.llm.budget import BudgetedLLMClient

    entered = asyncio.Event()
    release = asyncio.Event()

    class Inner:
        calls = 0

        async def complete(self, req: LLMRequest) -> LLMResponse:
            self.calls += 1
            entered.set()
            await release.wait()
            return LLMResponse("{}", "model", 3, 2, None, 0)

    inner = Inner()
    client = BudgetedLLMClient(inner, token_limit=5)
    first = asyncio.create_task(client.complete(request()))
    await entered.wait()
    second = asyncio.create_task(client.complete(request(LLMRole.CONVERSATION)))
    release.set()
    await first
    with pytest.raises(TokenBudgetExceeded):
        await second
    assert inner.calls == 1
    assert client.tokens_used("conversation-1") == 5
    await client.complete(replace(request(), conversation_id="another"))
    assert inner.calls == 2
    assert client.tokens_used("another") == 5


@pytest.mark.parametrize("first_latency", [0.4, 0.8])
def test_budget_deadline_includes_wait_for_conversation_lock(first_latency: float) -> None:
    import asyncio

    from application.llm import LLMResponse, LLMUnavailable
    from infrastructure.llm.budget import BudgetedLLMClient

    with virtual_time() as timeline:

        class Inner:
            calls = 0

            async def complete(self, req: LLMRequest) -> LLMResponse:
                self.calls += 1
                await timeline.sleep(first_latency if self.calls == 1 else 0.4)
                return LLMResponse("{}", "model", 1, 1, None, 0)

        async def run() -> None:
            inner = Inner()
            client = BudgetedLLMClient(inner)
            first = asyncio.create_task(client.complete(request(budget=1.0)))
            second = asyncio.create_task(client.complete(request(budget=0.5)))
            with pytest.raises(LLMUnavailable):
                await second
            assert timeline.monotonic() == 0.5
            await first
            assert inner.calls == (2 if first_latency == 0.4 else 1)
            # Cancellation neither leaks the lock nor charges an absent response.
            assert client.tokens_used("conversation-1") == 2
            await client.complete(request(budget=1.0))
            assert client.tokens_used("conversation-1") == 4

        timeline.run(run())
