"""Envelopes entre canais e núcleo; saída representa intenção de domínio."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, get_args

from domain.handoff import HandoffDecision, SlotName
from domain.product import ProductFacts
from domain.quote import Declined, Quote

type MessageType = Literal["text", "audio", "image", "document"]


@dataclass(frozen=True, slots=True)
class InboundMessage:
    channel: str
    conversation_id: str
    channel_user_id: str = field(repr=False)
    tipo: MessageType
    corpo: str = field(repr=False)
    provider_message_id: str
    indice: int
    media_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not all((self.channel, self.conversation_id, self.channel_user_id,
                    self.provider_message_id)) or self.indice < 0:
            raise ValueError("Envelope sem identidade ou índice válido")
        if self.tipo not in ("text", "audio", "image", "document"):
            raise ValueError("Tipo de mensagem inválido")

    @property
    def media_status(self) -> Literal["nao_resolvido"] | None:
        return None if self.tipo == "text" else "nao_resolvido"


class Intent(StrEnum):
    APRESENTAR_COTACAO = "apresentar_cotacao"
    PEDIR_DADO = "pedir_dado"
    RECUSAR = "recusar"
    ESCALAR = "escalar"


@dataclass(frozen=True, slots=True)
class ApresentarCotacao:
    quote: Quote
    facts: ProductFacts


@dataclass(frozen=True, slots=True)
class PedirDado:
    slot: SlotName

    def __post_init__(self) -> None:
        if self.slot not in get_args(SlotName.__value__):
            raise ValueError("Dado solicitado inválido")


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    conversation_id: str
    intent: Intent
    payload: ApresentarCotacao | PedirDado | Declined | HandoffDecision

    def __post_init__(self) -> None:
        if not self.conversation_id.strip():
            raise ValueError("Envelope sem identidade de conversa")
        if not isinstance(self.intent, Intent):
            raise ValueError("Intenção inválida")
        expected = {
            Intent.APRESENTAR_COTACAO: ApresentarCotacao,
            Intent.PEDIR_DADO: PedirDado,
            Intent.RECUSAR: Declined,
            Intent.ESCALAR: HandoffDecision,
        }
        if not isinstance(self.payload, expected[self.intent]):
            raise ValueError("Payload incompatível com a intenção")
        if self.intent == Intent.ESCALAR and isinstance(self.payload, HandoffDecision):
            if not self.payload.escalar:
                raise ValueError("Escalação exige decisão positiva")
