"""Adapters for the three external effects of a handoff decision."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

import httpx

from application.outbox import HandoffDeliveryError
from domain.handoff import HandoffDecision
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
        try:
            response = await self._client.post(
                self._url,
                headers={"Idempotency-Key": idempotency_key},
                json={
                    "conversation_id": conversation_id,
                    "decision": _serialize(decision),
                },
            )
            response.raise_for_status()
        except Exception:
            raise HandoffDeliveryError() from None


class WebhookHandoffSink(_HttpHandoffSink):
    pass


class QueueApiHandoffSink(_HttpHandoffSink):
    pass


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
