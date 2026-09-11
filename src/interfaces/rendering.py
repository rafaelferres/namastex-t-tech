"""Renderização textual compartilhada por adapters CLI e replay."""

from __future__ import annotations

from agent.templates import render_declined, render_handoff, render_quote
from domain.handoff import HandoffDecision
from domain.messages import ApresentarCotacao, MensagemConversacional, OutboundMessage, PedirDado
from domain.quote import Declined


def render_outbound(message: OutboundMessage) -> str:
    payload = message.payload
    if isinstance(payload, ApresentarCotacao):
        return render_quote(payload.quote, payload.facts)
    if isinstance(payload, Declined):
        return render_declined(payload)
    if isinstance(payload, HandoffDecision):
        return render_handoff()
    if isinstance(payload, MensagemConversacional):
        return payload.texto
    if isinstance(payload, PedirDado):
        names = {
            "idade": "sua idade",
            "veiculo_ano": "o ano-modelo do veículo",
            "cep": "seu CEP",
            "plano_id": "o plano desejado",
            "data_inicio": "a data desejada para início da vigência",
        }
        return f"Pode informar ou confirmar {names[payload.slot]}?"
    raise ValueError("Intenção sem renderização")
