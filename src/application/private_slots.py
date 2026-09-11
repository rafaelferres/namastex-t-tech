from __future__ import annotations

from typing import Protocol


class PrivateCepStore(Protocol):
    async def remember(self, conversation_id: str, cep: str) -> None:
        """Primeiro valor é imutável; implementação não persiste texto em claro."""
        ...

    async def read(self, conversation_id: str) -> str | None: ...
