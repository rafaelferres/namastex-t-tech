"""Linha do tempo de uma conversa, turno a turno, a partir do que foi persistido.

Base do log de execução (docs/execucao-completa.md): nada é reexecutado, só lido.
Todo texto já chega redigido: entrada do grafo, outbox e snapshot saem da ingestão.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from application.tracing import QuoteAttempt, TraceReader
from application.turns import TurnEvent, TurnReader
from domain.handoff import HandoffDecision
from domain.messages import OutboundMessage


class TurnStateReader(Protocol):
    async def read_states(self, conversation_id: str) -> tuple[Mapping[str, Any], ...]: ...


class LeadMessageReader(Protocol):
    async def lead_messages(self, conversation_id: str) -> Mapping[str, OutboundMessage]: ...


class HandoffReader(Protocol):
    async def handoff(self, identifier: str) -> HandoffDecision | None: ...


@dataclass(frozen=True, slots=True)
class SlotView:
    nome: str
    valor: str
    status: str
    proveniencia: str


@dataclass(frozen=True, slots=True)
class TurnReport:
    trace_id: str
    entrada: str
    slots: tuple[SlotView, ...]
    rota: tuple[str, ...]
    status: str
    pedido: str | None
    erro: str | None
    objecao: str | None
    objecao_fonte: str | None
    etapas: tuple[TurnEvent, ...]
    tentativas: tuple[QuoteAttempt, ...]
    resposta: OutboundMessage | None
    escalacao: HandoffDecision | None


class InspectConversation:
    def __init__(
        self,
        states: TurnStateReader,
        attempts: TraceReader,
        turns: TurnReader,
        messages: LeadMessageReader,
        handoffs: HandoffReader,
    ) -> None:
        self._states, self._attempts, self._turns = states, attempts, turns
        self._messages, self._handoffs = messages, handoffs

    async def execute(self, conversation_id: str) -> tuple[TurnReport, ...]:
        messages = await self._messages.lead_messages(conversation_id)
        reports: list[TurnReport] = []
        for state in await self._states.read_states(conversation_id):
            # O trace_id do turno costura estado, timeline, tentativas, outbox e handoff.
            trace = str(state["trace_id"])
            reports.append(
                TurnReport(
                    trace_id=trace,
                    entrada=str(state.get("entrada", "")),
                    slots=_slots(state.get("slots") or {}),
                    rota=tuple(state.get("rota") or ()),
                    status=str(state.get("status", "")),
                    pedido=state.get("pedido"),
                    erro=state.get("erro"),
                    objecao=state.get("objecao"),
                    objecao_fonte=state.get("objecao_fonte"),
                    etapas=await self._turns.read_turn(trace),
                    tentativas=await self._attempts.read(trace),
                    resposta=messages.get(trace),
                    escalacao=await self._handoffs.handoff(trace),
                )
            )
        return tuple(reports)


def _slots(values: Mapping[str, Any]) -> tuple[SlotView, ...]:
    return tuple(
        SlotView(name, str(item["valor"]), str(item["status"]), str(item["proveniencia"]))
        for name, item in values.items()
        if item is not None and item.get("valor") is not None
    )
