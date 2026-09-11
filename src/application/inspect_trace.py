from __future__ import annotations

from application.tracing import TraceReader


class InspectQuoteTrace:
    def __init__(self, reader: TraceReader) -> None:
        self._reader = reader

    async def execute(self, trace_id: str) -> str:
        events = await self._reader.read(trace_id)
        if not events:
            return "Trace não encontrado."
        lines = [f"Cotação {trace_id} | conversa {events[0].conversation_id}"]
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
        if not any(event.tentativa == 0 for event in events):
            lines.append("Desfecho não registrado.")
        return "\n".join(lines)
