from __future__ import annotations

import httpx
import pytest

from agent.schemas.slots import Slots
from application.ports import SystemClock
from infrastructure.llm.config import LLMConfig
from infrastructure.privacy import PrivacyRedactor
from infrastructure.wiring import build_slot_extractor


@pytest.mark.asyncio
async def test_wired_extractor_uses_configured_model_and_token_handoff():
    models = []

    def respond(request):
        import json
        models.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={
            "model": "extractor-model", "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 3, "cost": 0.0001},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        extractor = build_slot_extractor(
            client=http, config=LLMConfig(api_key="unit-test", extractor_model="extractor-model",
                                        conversation_model="conversation-model",
                                        conversation_token_limit=5),
            clock=SystemClock(), privacy=PrivacyRedactor(),
        )
        result = await extractor.extract("Tenho 30 anos", Slots(), conversation_id="conv")
    assert result.tokens_esgotados
    assert models == ["extractor-model"]
