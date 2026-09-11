from __future__ import annotations

import asyncio
import json
import math
from decimal import Decimal

import httpx

from application.llm import LLMContractError, LLMRequest, LLMResponse, LLMToolCall, LLMUnavailable
from application.ports import Clock
from infrastructure.llm.config import LLMConfig


class OpenRouterLLMClient:
    def __init__(self, client: httpx.AsyncClient, config: LLMConfig, clock: Clock) -> None:
        self._client = client
        self._config = config
        self._clock = clock

    async def complete(self, request: LLMRequest) -> LLMResponse:
        start = self._clock.monotonic()
        budget = min(request.budget, self._config.budget_seconds)
        if not math.isfinite(request.budget) or budget <= 0:
            raise LLMUnavailable()
        try:
            async with asyncio.timeout(budget):
                response = await self._client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                    json={
                        "model": self._config.model_for(request.role),
                        "messages": [
                            {"role": "system", "content": request.system},
                            {"role": "user", "content": request.user},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "structured_response",
                                "strict": True,
                                "schema": request.schema,
                            },
                        },
                        "provider": {"require_parameters": True},
                        **(
                            {
                                "tools": [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": tool.name,
                                            "description": tool.description,
                                            "parameters": tool.parameters,
                                            "strict": True,
                                        },
                                    }
                                    for tool in request.tools
                                ],
                                # Sem parallel_tool_calls: com require_parameters nenhum
                                # endpoint o aceita (404). Mais de uma chamada já é erro
                                # de contrato no conversador.
                            }
                            if request.tools
                            else {}
                        ),
                    },
                    timeout=min(self._config.timeout_seconds, budget),
                    follow_redirects=False,
                )
        except (httpx.RequestError, TimeoutError):
            raise LLMUnavailable() from None
        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            raise LLMUnavailable()
        if response.status_code != 200:
            raise LLMContractError()
        try:
            body = response.json(parse_float=Decimal)
            usage = body["usage"]
            message = body["choices"][0]["message"]
            content = message.get("content")
            calls = parse_tool_calls(message.get("tool_calls", []))
            if content is None and calls:
                content = ""
            if calls and not request.tools:
                raise ValueError
            model = body["model"]
            prompt_tokens = usage["prompt_tokens"]
            completion_tokens = usage["completion_tokens"]
            cost = usage.get("cost")
            if (
                not isinstance(content, str)
                or not isinstance(model, str)
                or not model
                or type(prompt_tokens) is not int
                or prompt_tokens < 0
                or type(completion_tokens) is not int
                or completion_tokens < 0
            ):
                raise ValueError
            if cost is not None:
                if type(cost) not in (Decimal, int):
                    raise ValueError
                cost = Decimal(cost)
                if not cost.is_finite() or cost < 0:
                    raise ValueError
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMContractError() from None
        return LLMResponse(
            content,
            model,
            prompt_tokens,
            completion_tokens,
            cost,
            (self._clock.monotonic() - start) * 1000,
            calls,
        )


def parse_tool_calls(value: object) -> tuple[LLMToolCall, ...]:
    """Validate provider tool envelopes without exposing their contents in errors."""
    if not isinstance(value, list):
        raise ValueError("tool envelope")
    result = []
    for item in value:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise ValueError("tool envelope")
        function = item.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ValueError("tool envelope")
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise ValueError("tool arguments")
        parsed = json.loads(arguments)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments")
        result.append(LLMToolCall(function["name"], parsed))
    return tuple(result)
