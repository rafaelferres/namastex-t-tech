from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Lead:
    id: str
    channel: str
    channel_user_id: str
    cpf_hash: str | None


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    lead_id: str
    status: str
    objecoes_preco: int = 0
