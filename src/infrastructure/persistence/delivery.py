from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from application.tracing import QuoteAttempt
from domain.handoff import (
    CollectedSlot,
    HandoffDecision,
    HandoffReason,
    HandoffSnapshot,
    HandoffSuggestion,
)
from domain.messages import ApresentarCotacao, Intent, OutboundMessage, PedirDado
from domain.product import ProductFacts
from domain.quote import Carencia, Declined, ProRata, Quote
from infrastructure.persistence._worker import run_sqlite
from infrastructure.privacy import PrivacyRedactor

_TYPES = {
    cls.__name__: cls
    for cls in (
        QuoteAttempt,
        CollectedSlot,
        HandoffDecision,
        HandoffSnapshot,
        HandoffSuggestion,
        ApresentarCotacao,
        OutboundMessage,
        PedirDado,
        ProductFacts,
        Carencia,
        Declined,
        ProRata,
        Quote,
    )
}


_TEXT_FIELDS = {
    CollectedSlot: {"valor"},
    QuoteAttempt: {"erro"},
    Declined: {"motivo"},
    Carencia: {"observacao", "coberturas"},
    Quote: {"plano_nome", "coberturas"},
    ProductFacts: {"nome", "coberturas"},
}


def _redact_text(value: Any) -> Any:
    if isinstance(value, str):
        return PrivacyRedactor().redact(value)
    if isinstance(value, tuple):
        return tuple(_redact_text(item) for item in value)
    return value


def _pack(value: Any) -> Any:
    if isinstance(value, HandoffSnapshot):
        # Persist only complete trace records; the domain's read-only view is broader.
        if any(type(attempt) is not QuoteAttempt for attempt in value.tentativas):
            raise ValueError("Snapshot exige registros QuoteAttempt")
        if "cep" in value.slots:
            value = replace(
                value,
                slots={
                    **value.slots,
                    "cep": replace(value.slots["cep"], valor="[CEP REDIGIDO]"),
                },
            )
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        if type(value) not in _TYPES.values():
            raise ValueError("Tipo de payload persistível inválido")
        text_fields = _TEXT_FIELDS.get(type(value), set())
        return {
            "$type": type(value).__name__,
            "fields": {
                field.name: _pack(
                    _redact_text(getattr(value, field.name))
                    if field.name in text_fields
                    else getattr(value, field.name)
                )
                for field in fields(value)
            },
        }
    if isinstance(value, Mapping):
        return {key: _pack(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return {"$tuple": [_pack(item) for item in value]}
    return value


def _unpack(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "$decimal" in value:
        return Decimal(value["$decimal"])
    if "$datetime" in value:
        return datetime.fromisoformat(value["$datetime"])
    if "$date" in value:
        return date.fromisoformat(value["$date"])
    if "$tuple" in value:
        return tuple(_unpack(item) for item in value["$tuple"])
    if "$type" in value:
        cls = _TYPES[value["$type"]]
        kwargs = {key: _unpack(item) for key, item in value["fields"].items()}
        if cls is OutboundMessage:
            kwargs["intent"] = Intent(kwargs["intent"])
        if cls in (HandoffDecision, HandoffSnapshot, HandoffSuggestion) and kwargs.get("motivo"):
            kwargs["motivo"] = HandoffReason(kwargs["motivo"])
        return cls(**kwargs)
    return {key: _unpack(item) for key, item in value.items()}


class SQLiteDelivery:
    """Intenções e decisões redigidas; snapshots exigem registros QuoteAttempt completos.

    O protocolo de leitura do domínio admite views menores, mas só registros do
    trace existente têm codec durável. Views incompatíveis são rejeitadas antes
    da escrita. Entrega fica para outro caso de uso.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()

    async def enqueue(
        self,
        message: OutboundMessage,
        destino: Literal["lead", "webhook_vendas", "api_fila"],
        now: datetime,
    ) -> str:
        identifier = uuid4().hex
        await run_sqlite(
            partial(
                self._enqueue,
                identifier,
                message.conversation_id,
                json.dumps(_pack(message)),
                destino,
                now.isoformat(),
            )
        )
        return identifier

    def _enqueue(
        self, identifier: str, conversation_id: str, payload: str, destino: str, now: str
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO outbound_messages "
                "(id, conversation_id, payload, destino, status, criado_em) "
                "VALUES (?, ?, ?, ?, 'pendente', ?)",
                (identifier, conversation_id, payload, destino, now),
            )

    async def outbound(self, identifier: str) -> OutboundMessage | None:
        serialized = await run_sqlite(
            partial(self._read, "outbound_messages", "payload", identifier)
        )
        if serialized is None:
            return None
        result = _unpack(json.loads(serialized))
        if not isinstance(result, OutboundMessage):
            raise ValueError("Intenção persistida inválida")
        return result

    async def record_handoff(
        self, conversation_id: str, decision: HandoffDecision, now: datetime
    ) -> str:
        if not decision.escalar or decision.motivo is None or decision.snapshot is None:
            raise ValueError("Handoff exige decisão de escalar com snapshot")
        identifier = uuid4().hex
        serialized = json.dumps(_pack(decision))
        await run_sqlite(
            partial(
                self._handoff, identifier, conversation_id, decision, serialized, now.isoformat()
            )
        )
        return identifier

    def _handoff(
        self,
        identifier: str,
        conversation_id: str,
        decision: HandoffDecision,
        serialized: str,
        now: str,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO handoffs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    conversation_id,
                    decision.motivo,
                    bool(decision.sugestao_llm and decision.sugestao_llm.escalar),
                    serialized,
                    now,
                ),
            )

    async def handoff(self, identifier: str) -> HandoffDecision | None:
        serialized = await run_sqlite(partial(self._read, "handoffs", "snapshot", identifier))
        if serialized is None:
            return None
        result = _unpack(json.loads(serialized))
        if not isinstance(result, HandoffDecision):
            raise ValueError("Decisão persistida inválida")
        return result

    def _read(self, table: str, column: str, identifier: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {column} FROM {table} WHERE id=?",
                (identifier,),
            ).fetchone()
            return str(row[0]) if row else None
