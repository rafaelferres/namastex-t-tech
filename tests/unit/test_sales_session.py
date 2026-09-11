from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from application.ingest import IngestedTurn
from application.sales import SalesSession
from domain.messages import Intent, MensagemConversacional, OutboundMessage
from interfaces.rendering import render_outbound
from tests.fakes import FakeClock


@pytest.mark.asyncio
async def test_case_use_enqueues_domain_intent_and_adapter_renders():
    message = OutboundMessage("c", Intent.CONVERSAR, MensagemConversacional("Como posso ajudar?"))
    engine = AsyncMock(respond=AsyncMock(return_value=message))
    queue = AsyncMock()
    usecase = SalesSession(engine, queue, FakeClock())
    turn = IngestedTurn("c", (), 0)
    await usecase.consume(turn)
    engine.respond.assert_awaited_once_with(turn)
    assert queue.enqueue.call_args.args[:2] == (message, "lead")
    assert render_outbound(message) == "Como posso ajudar?"
