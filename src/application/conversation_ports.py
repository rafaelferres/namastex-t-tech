from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.conversations import Conversation, Lead
from domain.messages import InboundMessage


class ConversationReader(Protocol):
    async def get(self, conversation_id: str) -> Conversation | None: ...


class ConversationWriter(Protocol):
    async def ensure(
        self, message: InboundMessage, cpf_hash: str | None, now: datetime
    ) -> Conversation: ...
    async def add_price_objection(self, conversation_id: str) -> int: ...


class MessageWriter(Protocol):
    async def add(self, message: InboundMessage, now: datetime) -> bool: ...


class MessageReader(Protocol):
    async def messages(self, conversation_id: str) -> tuple[InboundMessage, ...]: ...


class LeadReader(Protocol):
    async def lead(self, channel: str, channel_user_id: str) -> Lead | None: ...
