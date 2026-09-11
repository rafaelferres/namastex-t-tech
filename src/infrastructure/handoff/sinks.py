"""Adapters for the three external effects of a handoff decision."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

import httpx

from application.outbox import HandoffConfigurationError, HandoffDeliveryError
from domain.handoff import HandoffDecision
from infrastructure.http_errors import describe, describe_transport, is_configuration
from infrastructure.privacy import PrivacyRedactor

type LeadCallback = Callable[[str, HandoffDecision, str], Awaitable[None]]


class LeadCallbackHandoffSink:
    def __init__(self, callback: LeadCallback) -> None:
        self._callback = callback

    async def emit(
        self,
        decision: HandoffDecision,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> None:
        await self._callback(conversation_id, decision, idempotency_key)


class _HttpHandoffSink:
    servico = "handoff_http"

    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client = client
        self._url = url

    async def emit(
        self,
        decision: HandoffDecision,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> None:
        payload = {"conversation_id": conversation_id, "decision": _serialize(decision)}
        try:
            response = await self._client.post(
                self._url, headers={"Idempotency-Key": idempotency_key}, json=payload
            )
        except httpx.RequestError as error:
            raise HandoffDeliveryError(describe_transport(error)) from None
        if response.is_success:
            return
        # Status e corpo redigido acompanham a falha até a outbox (D-035).
        detail = describe(response)
        if is_configuration(response.status_code):
            raise HandoffConfigurationError(self.servico, detail)
        raise HandoffDeliveryError(detail)

    async def probe(self) -> None:
        """Chamada mínima de partida: rota inexistente ou credencial rejeitada falha alto."""
        try:
            response = await self._client.request("OPTIONS", self._url)
        except httpx.RequestError as error:
            raise HandoffDeliveryError(describe_transport(error)) from None
        if response.status_code in (401, 403, 404):
            raise HandoffConfigurationError(self.servico, describe(response))


class WebhookHandoffSink(_HttpHandoffSink):
    servico = "webhook_vendas"


class QueueApiHandoffSink(_HttpHandoffSink):
    servico = "api_fila"


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, str):
        return PrivacyRedactor().redact(value)
    return value
