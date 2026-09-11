from __future__ import annotations

from application.tracing import TraceReader
from application.turns import TurnReader


class InspectQuoteTrace:
    def __init__(self, reader: TraceReader, turns: TurnReader | None = None) -> None:
        self._reader = reader
        self._turns = turns

    async def execute(self, trace_id: str) -> str:
        events = await self._reader.read(trace_id)
        turns = await self._turns.read_turn(trace_id) if self._turns is not None else ()
        if not events and not turns:
            return "Trace não encontrado."
        conversation = events[0].conversation_id if events else turns[0].conversation_id
        lines = [f"Cotação {trace_id} | conversa {conversation}"]
        for turn in turns:
            lines.append(
                f"Etapa {turn.etapa}: {turn.status} | {turn.latencia_ms} ms"
                f" | erro={turn.erro or '—'}"
            )
        for event in events:
            label = f"Tentativa {event.tentativa}" if event.tentativa else "Desfecho"
            http = str(event.http_status) if event.http_status is not None else "—"
            flags = (" | hedge" if event.hedge else "") + (
                " | ano normalizado" if event.ano_normalizado else ""
            )
            lines.append(
                f"{label}: {event.status} | {event.latencia_ms} ms | origem={event.origem}"
                f" | HTTP {http}{flags} | erro={event.erro or '—'}"
            )
        if turns:
            lines.append(f"Desfecho do turno: {turns[-1].status}")
        elif not any(event.tentativa == 0 for event in events):
            lines.append("Desfecho não registrado.")
        return "\n".join(lines)
