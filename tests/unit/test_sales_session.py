from __future__ import annotations

from datetime import timedelta
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


def session_with(closer):
    message = OutboundMessage("c", Intent.CONVERSAR, MensagemConversacional("Oi"))
    engine = AsyncMock(respond=AsyncMock(return_value=message))
    queue, clock = AsyncMock(), FakeClock()
    session = SalesSession(engine, queue, clock, closer=closer, retention=timedelta(hours=24))
    return session, engine, queue, clock


@pytest.mark.asyncio
async def test_each_turn_purges_conversations_idle_past_retention():
    closer = AsyncMock(stale=AsyncMock(return_value=("abandonada",)))
    session, engine, _, clock = session_with(closer)
    await session.consume(IngestedTurn("c", (), 0))
    closer.stale.assert_awaited_once_with(clock.now() - timedelta(hours=24))
    engine.forget.assert_awaited_once_with("abandonada")
    closer.close.assert_awaited_once_with("abandonada", clock.now())


@pytest.mark.asyncio
async def test_purge_failure_never_costs_the_lead_reply():
    closer = AsyncMock(stale=AsyncMock(side_effect=RuntimeError("database is locked")))
    session, _, queue, _ = session_with(closer)
    await session.consume(IngestedTurn("c", (), 0))
    assert queue.enqueue.call_args.args[1] == "lead"
