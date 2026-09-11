from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from application.llm import LLMContractError, LLMTool, LLMToolCall
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.llm.recording import RecordedLLMClient
from tests.unit.test_llm_client import request
from tests.virtual_time import virtual_time

TOOL = LLMTool(
    "cotar",
    "Solicita cotação",
    {
        "type": "object",
        "properties": {"plano_id": {"type": "string"}},
        "required": ["plano_id"],
        "additionalProperties": False,
    },
)


@pytest.mark.parametrize("arguments", ['{"plano_id":"basico"}', "not-json", "[]"])
def test_native_tool_request_and_response(arguments):
    with virtual_time() as clock:

        def handle(req):
            body = json.loads(req.content)
            assert body["tools"][0]["function"]["parameters"] == TOOL.parameters
            # Com require_parameters, nenhum endpoint do OpenRouter aceita esse campo (404).
            assert "parallel_tool_calls" not in body
            return httpx.Response(
                200,
                json={
                    "model": "m",
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "cotar", "arguments": arguments},
                                    }
                                ],
                            }
                        }
                    ],
                },
            )

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                client = OpenRouterLLMClient(http, LLMConfig(api_key="test"), clock)
                req = replace(request(), tools=(TOOL,))
                if arguments != '{"plano_id":"basico"}':
                    with pytest.raises(LLMContractError):
                        await client.complete(req)
                else:
                    result = await client.complete(req)
                    assert result.content == ""
                    assert result.tool_calls == (LLMToolCall("cotar", {"plano_id": "basico"}),)

        clock.run(run())


@pytest.mark.asyncio
async def test_tool_capture_roundtrip_and_legacy_digest(tmp_path):
    from application.llm import LLMResponse

    req = request()
    legacy = {
        "version": 1,
        "position": 0,
        "model": "m",
        "settings": {},
        "request": {
            "conversation_id": req.conversation_id,
            "role": req.role,
            "system": req.system,
            "user": req.user,
            "schema": req.schema,
            "budget": req.budget,
        },
    }
    digest = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    response = LLMResponse("{}", "m", 1, 2, None, 0)
    leaf = AsyncMock(complete=AsyncMock(return_value=response))
    models = {req.role: "m"}
    await RecordedLLMClient(leaf, tmp_path, "record", models).complete(req)
    assert (tmp_path / f"{digest}.json").exists()
    tool_response = replace(
        response, content="", tool_calls=(LLMToolCall("cotar", {"plano_id": "basico"}),)
    )
    leaf.complete.return_value = tool_response
    tool_req = replace(req, tools=(TOOL,))
    await RecordedLLMClient(leaf, tmp_path, "record", models).complete(tool_req)
    assert (
        await RecordedLLMClient(None, tmp_path, "replay", models).complete(tool_req)
        == tool_response
    )


@pytest.mark.asyncio
async def test_untrusted_tool_arguments_are_redacted_before_capture(tmp_path):
    from application.llm import LLMResponse

    response = LLMResponse(
        "", "m", 1, 1, None, 0, (LLMToolCall("cotar", {"plano_id": "private@example.com"}),)
    )
    leaf = AsyncMock(complete=AsyncMock(return_value=response))
    req = replace(request(), tools=(TOOL,))
    await RecordedLLMClient(leaf, tmp_path, "record", {req.role: "m"}).complete(req)
    assert "private@example.com" not in next(tmp_path.glob("*.json")).read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("cep", [12345678, 1310100])
async def test_numeric_pii_tool_arguments_record_and_replay_as_valid_json(tmp_path, cep):
    from application.llm import LLMResponse

    arguments = {
        "plano_id": "basico",
        "cep": cep,
        "nested": [{"cep": cep, "email": "private@example.com"}, 12345678, True, None, 18],
    }
    response = LLMResponse("", "m", 1, 1, None, 0, (LLMToolCall("cotar", arguments),))
    leaf = AsyncMock(complete=AsyncMock(return_value=response))
    req = replace(request(), tools=(TOOL,))
    models = {req.role: "m"}
    recorded = await RecordedLLMClient(leaf, tmp_path, "record", models).complete(req)
    assert recorded.tool_calls[0].arguments == {
        "plano_id": "basico",
        "cep": "[CEP]",
        "nested": [{"cep": "[CEP]", "email": "[EMAIL]"}, "[CEP]", True, None, 18],
    }
    fixture = next(tmp_path.glob("*.json")).read_text()
    assert str(cep) not in fixture
    assert "private@example.com" not in fixture
    assert await RecordedLLMClient(None, tmp_path, "replay", models).complete(req) == recorded


@pytest.mark.asyncio
async def test_recorded_numeric_extra_argument_still_reaches_converser_contract_error(tmp_path):
    from agent.nodes.converse import ConversationInput, Converser
    from application.llm import LLMResponse, LLMRole

    response = LLMResponse(
        "",
        "m",
        1,
        1,
        None,
        0,
        (LLMToolCall("cotar", {"plano_id": "basico", "cep": 12345678}),),
    )
    leaf = AsyncMock(complete=AsyncMock(return_value=response))
    recorder = RecordedLLMClient(leaf, tmp_path, "record", {LLMRole.CONVERSATION: "m"})
    with pytest.raises(LLMContractError):
        await Converser(recorder).converse(ConversationInput("c", "", (), ()))
