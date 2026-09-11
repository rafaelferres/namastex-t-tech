from __future__ import annotations

import json

import httpx
import pytest

from domain.handoff import ConversationContext, HandoffDecision, HandoffPolicy


def _decision() -> HandoffDecision:
    return HandoffPolicy().evaluate(ConversationContext(pede_humano=True))


@pytest.mark.asyncio
async def test_lead_sink_forwards_delivery_context() -> None:
    from infrastructure.handoff import LeadCallbackHandoffSink

    received: list[tuple[str, str, HandoffDecision]] = []

    async def callback(
        conversation_id: str, decision: HandoffDecision, idempotency_key: str
    ) -> None:
        received.append((conversation_id, idempotency_key, decision))

    sink = LeadCallbackHandoffSink(callback)
    decision = _decision()
    await sink.emit(decision, conversation_id="conv-a", idempotency_key="trace-1:lead")

    assert received == [("conv-a", "trace-1:lead", decision)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sink_name", "url"),
    [
        ("WebhookHandoffSink", "https://sales.test/handoffs"),
        ("QueueApiHandoffSink", "https://queue.test/items"),
    ],
)
async def test_http_sinks_send_snapshot_with_idempotency_key(sink_name: str, url: str) -> None:
    import infrastructure.handoff as handoff_adapters

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        sink_type = getattr(handoff_adapters, sink_name)
        sink = sink_type(http, url)
        await sink.emit(
            _decision(), conversation_id="conv-a", idempotency_key="trace-1:webhook_vendas"
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.headers["Idempotency-Key"] == "trace-1:webhook_vendas"
    assert json.loads(request.content) == {
        "conversation_id": "conv-a",
        "decision": {
            "escalar": True,
            "motivo": "pedido_de_humano",
            "sugestao_llm": None,
            "divergencia": True,
            "snapshot": {
                "slots": {},
                "tentativas": [],
                "motivo": "pedido_de_humano",
            },
        },
    }


@pytest.mark.asyncio
async def test_http_sink_raises_generic_error_without_response_body() -> None:
    from application.outbox import HandoffDeliveryError
    from infrastructure.handoff import WebhookHandoffSink

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(503, text="pessoa@example.com CEP 01310-100")
        )
    ) as http:
        sink = WebhookHandoffSink(http, "https://sales.test/handoffs")
        with pytest.raises(HandoffDeliveryError) as raised:
            await sink.emit(
                _decision(), conversation_id="conv-a", idempotency_key="trace-1:webhook_vendas"
            )

    assert str(raised.value) == "handoff_delivery_failed"
