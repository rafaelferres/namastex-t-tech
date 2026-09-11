"""Porta de resolução de mídia; atua na ingestão, antes do agente."""

from __future__ import annotations

from typing import Protocol

from domain.messages import InboundMessage, MediaResolution


class MediaResolver(Protocol):
    async def resolve(self, message: InboundMessage) -> MediaResolution | None: ...
