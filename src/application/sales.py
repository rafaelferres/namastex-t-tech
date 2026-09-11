from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from application.ingest import IngestedTurn
from application.ports import Clock
from domain.messages import Intent, OutboundMessage


class TurnEngine(Protocol):
    async def respond(self, turn: IngestedTurn) -> OutboundMessage: ...

    async def forget(self, conversation_id: str) -> None: ...


class ConversationCloser(Protocol):
    async def close(self, conversation_id: str, now: datetime) -> None: ...


class OutboundWriter(Protocol):
    async def enqueue(
        self,
        message: OutboundMessage,
        destino: Literal["lead", "webhook_vendas", "api_fila"],
        now: datetime,
        *, identifier: str | None = None,
    ) -> str: ...


class SalesSession:
    def __init__(
        self,
        engine: TurnEngine,
        writer: OutboundWriter,
        clock: Clock,
        *,
        closer: ConversationCloser | None = None,
    ) -> None:
        self._engine, self._writer, self._clock = engine, writer, clock
        self._closer = closer
        self._responses: dict[str, OutboundMessage] = {}

    async def consume(self, turn: IngestedTurn) -> None:
        message = await self._engine.respond(turn)
        # Handoff persistence atomically enqueues its own lead notification.
        if message.intent is not Intent.ESCALAR:
            identifier = (f"{turn.conversation_id}:{turn.messages[-1].provider_message_id}:reply"
                          if turn.messages else None)
            await self._writer.enqueue(message, "lead", self._clock.now(), identifier=identifier)
        self._responses[turn.conversation_id] = message
        if message.intent is Intent.RECUSAR:
            # Recusa é resposta final (invariante 5): a conversa encerra e os slots saem.
            await self.close(turn.conversation_id)

    async def close(self, conversation_id: str) -> None:
        """Encerramento: purga estado do grafo e slots operacionais (retenção, D-036)."""
        await self._engine.forget(conversation_id)
        if self._closer is not None:
            await self._closer.close(conversation_id, self._clock.now())

    def latest_response(self, conversation_id: str) -> OutboundMessage | None:
        return self._responses.get(conversation_id)
