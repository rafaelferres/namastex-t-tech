from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TurnEvent:
    trace_id: str
    conversation_id: str
    etapa: str
    status: str
    latencia_ms: int
    erro: str | None
    criado_em: datetime
    # Só no evento "decisao": a sugestão do conversador ao lado da decisão da política.
    sugestao: str | None = None


class TurnRecorder(Protocol):
    def record(self, event: TurnEvent) -> None: ...


class TurnReader(Protocol):
    async def read_turn(self, trace_id: str) -> tuple[TurnEvent, ...]: ...
