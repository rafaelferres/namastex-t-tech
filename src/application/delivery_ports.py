from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from domain.handoff import HandoffDecision
from domain.messages import OutboundMessage


class OutboundWriter(Protocol):
    async def enqueue(
        self,
        message: OutboundMessage,
        destino: Literal["lead", "webhook_vendas", "api_fila"],
        now: datetime,
    ) -> str: ...


class OutboundReader(Protocol):
    async def outbound(self, identifier: str) -> OutboundMessage | None: ...


class HandoffWriter(Protocol):
    async def record_handoff(
        self, conversation_id: str, decision: HandoffDecision, now: datetime
    ) -> str: ...


class HandoffReader(Protocol):
    async def handoff(self, identifier: str) -> HandoffDecision | None: ...
