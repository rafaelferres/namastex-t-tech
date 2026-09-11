from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
from decimal import Decimal

import httpx

from application.llm import (
    LLMConfigurationError,
    LLMContractError,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    LLMUnavailable,
)
from application.ports import Clock
from infrastructure.http_errors import (
    describe,
    describe_transport,
    is_configuration,
    is_transient,
)
from infrastructure.llm.config import LLMConfig

logger = logging.getLogger(__name__)


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
                            {"role": "user", "content": _user_content(request)},
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
        except (httpx.RequestError, TimeoutError) as error:
            detail = describe_transport(error)
            logger.warning("llm_http_unavailable %s", detail)
            raise LLMUnavailable(detalhe=detail) from None
        status = response.status_code
        if status != 200:
            # Classificação explícita; o corpo nunca é descartado (D-035).
            detail = describe(response)
            if is_transient(status):
                logger.warning("llm_http_unavailable %s", detail)
                raise LLMUnavailable(detalhe=detail)
            if is_configuration(status):
                logger.error("llm_http_configuration %s", detail)
                raise LLMConfigurationError("openrouter", detail)
            logger.error("llm_http_unexpected_status %s", detail)
            raise LLMContractError(detalhe=detail)
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
            detail = describe(response)
            logger.error("llm_response_contract %s", detail)
            raise LLMContractError(detalhe=detail) from None
        return LLMResponse(
            content,
            model,
            prompt_tokens,
            completion_tokens,
            cost,
            (self._clock.monotonic() - start) * 1000,
            calls,
        )


def _user_content(request: LLMRequest) -> object:
    if not request.anexos:
        return request.user
    parts: list[dict[str, object]] = [{"type": "text", "text": request.user}]
    for item in request.anexos:
        encoded = base64.b64encode(item.dados).decode()
        if item.tipo == "imagem":
            url = f"data:{item.formato};base64,{encoded}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            parts.append(
                {"type": "input_audio", "input_audio": {"data": encoded, "format": item.formato}}
            )
    return parts


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
