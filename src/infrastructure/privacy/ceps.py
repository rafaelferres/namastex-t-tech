"""CEP privado fora de prompt, log e banco."""

from __future__ import annotations


class MemoryPrivateCeps:
    # ponytail: memória do processo; restart perde o CEP e o grafo recusa cotar sem ele
    # (cep_coletado). Persistência cifrada quando houver decisão sobre CEP durável.
    def __init__(self) -> None:
        self._ceps: dict[str, str] = {}

    async def remember(self, conversation_id: str, cep: str) -> None:
        self._ceps.setdefault(conversation_id, cep)  # o primeiro valor é imutável

    async def read(self, conversation_id: str) -> str | None:
        return self._ceps.get(conversation_id)
