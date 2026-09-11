from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime
from functools import partial
from threading import Lock
from typing import cast
from uuid import uuid4

from domain.conversations import Conversation, Lead
from domain.messages import InboundMessage, MessageType
from infrastructure.persistence._worker import run_sqlite


def _identity(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class SQLiteConversations:
    """Conexão dedicada; só recebe corpo já redigido pela ingestão."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()

    async def get(self, conversation_id: str) -> Conversation | None:
        return await run_sqlite(partial(self._get, conversation_id))

    def _get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, lead_id, status, objecoes_preco FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            return Conversation(*row) if row else None

    async def lead(self, channel: str, channel_user_id: str) -> Lead | None:
        return await run_sqlite(partial(self._lead, channel, _identity(channel_user_id)))

    def _lead(self, channel: str, identity: str) -> Lead | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, channel, channel_user_id, cpf_hash FROM leads "
                "WHERE channel=? AND channel_user_id=?",
                (channel, identity),
            ).fetchone()
            return Lead(*row) if row else None

    async def ensure(
        self, message: InboundMessage, cpf_hash: str | None, now: datetime
    ) -> Conversation:
        if cpf_hash is not None and re.fullmatch(r"[0-9a-f]{64}", cpf_hash) is None:
            raise ValueError("Hash de CPF inválido")
        return await run_sqlite(partial(self._ensure, message, cpf_hash, now.isoformat()))

    def _ensure(self, message: InboundMessage, cpf_hash: str | None, now: str) -> Conversation:
        identity = _identity(message.channel_user_id)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT l.channel, l.channel_user_id FROM conversations c "
                "JOIN leads l ON l.id=c.lead_id WHERE c.id=?",
                (message.conversation_id,),
            ).fetchone()
            if existing is not None and existing != (message.channel, identity):
                raise ValueError("Identidade da conversa não pode mudar")
            lead = self._connection.execute(
                "SELECT id, cpf_hash FROM leads WHERE channel=? AND channel_user_id=?",
                (message.channel, identity),
            ).fetchone()
            if lead is None:
                lead_id = uuid4().hex
                self._connection.execute(
                    "INSERT INTO leads VALUES (?, ?, ?, ?, ?)",
                    (lead_id, message.channel, identity, cpf_hash, now),
                )
            else:
                lead_id, known_hash = lead
                if cpf_hash is not None and known_hash is not None and cpf_hash != known_hash:
                    raise ValueError("Hash de CPF já conhecido não pode mudar")
                self._connection.execute(
                    "UPDATE leads SET cpf_hash=coalesce(cpf_hash, ?) WHERE id=?",
                    (cpf_hash, lead_id),
                )
            self._connection.execute(
                "INSERT INTO conversations (id, lead_id, status, iniciada_em, atualizada_em) "
                "VALUES (?, ?, 'ativa', ?, ?) ON CONFLICT(id) "
                # Lead que volta reabre: sem isso a conversa sairia da purga por inatividade.
                "DO UPDATE SET atualizada_em=excluded.atualizada_em, "
                "status=CASE WHEN status='encerrada' THEN 'ativa' ELSE status END",
                (message.conversation_id, lead_id, now, now),
            )
            row = self._connection.execute(
                "SELECT id, lead_id, status, objecoes_preco FROM conversations WHERE id=?",
                (message.conversation_id,),
            ).fetchone()
            return Conversation(*row)

    async def add_price_objection(self, conversation_id: str) -> int:
        return await run_sqlite(partial(self._objection, conversation_id))

    def _objection(self, conversation_id: str) -> int:
        with self._lock, self._connection:
            row = self._connection.execute(
                "UPDATE conversations SET objecoes_preco=objecoes_preco+1 WHERE id=? "
                "RETURNING objecoes_preco",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Conversa não encontrada")
            return int(row[0])

    async def add(self, message: InboundMessage, now: datetime) -> bool:
        return await run_sqlite(partial(self._add, message, now.isoformat()))

    def _add(self, message: InboundMessage, now: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, 'inbound', ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_message_id) WHERE provider_message_id IS NOT NULL DO NOTHING",
                (
                    uuid4().hex,
                    message.conversation_id,
                    message.indice,
                    message.tipo,
                    message.corpo,
                    message.media_status,
                    message.provider_message_id,
                    now,
                ),
            )
            return cursor.rowcount == 1

    async def remember(self, conversation_id: str, cep: str) -> None:
        """Slot é dado operacional: o CEP fica em conversations.slots, primeiro valor imutável.

        Mensagem é log e continua redigida; o slot existe para cotar e sai no encerramento.
        """
        await run_sqlite(partial(self._remember, conversation_id, cep))

    def _remember(self, conversation_id: str, cep: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE conversations SET slots=json_set(slots, '$.cep', ?) "
                "WHERE id=? AND json_extract(slots, '$.cep') IS NULL",
                (cep, conversation_id),
            )

    async def read(self, conversation_id: str) -> str | None:
        return await run_sqlite(partial(self._read_cep, conversation_id))

    def _read_cep(self, conversation_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT json_extract(slots, '$.cep') FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    async def close(self, conversation_id: str, now: datetime) -> None:
        """Retenção: encerrar a conversa purga os slots operacionais (D-036)."""
        await run_sqlite(partial(self._close, conversation_id, now.isoformat()))

    def _close(self, conversation_id: str, now: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE conversations SET slots='{}', status='encerrada', atualizada_em=? "
                "WHERE id=?",
                (now, conversation_id),
            )

    async def stale(self, before: datetime) -> tuple[str, ...]:
        """Conversas abertas sem mensagem desde `before`: o lead abandonou (D-038)."""
        return await run_sqlite(partial(self._stale, before.isoformat()))

    def _stale(self, before: str) -> tuple[str, ...]:
        # ponytail: varredura sem índice por turno; índice em atualizada_em se a tabela crescer.
        with self._lock:
            rows = self._connection.execute(
                "SELECT id FROM conversations WHERE status<>'encerrada' AND atualizada_em<? "
                "ORDER BY id",
                (before,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    async def messages(self, conversation_id: str) -> tuple[InboundMessage, ...]:
        return await run_sqlite(partial(self._messages, conversation_id))

    def _messages(self, conversation_id: str) -> tuple[InboundMessage, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT l.channel, m.conversation_id, l.channel_user_id, m.tipo, m.corpo, "
                "m.provider_message_id, m.indice FROM messages m "
                "JOIN conversations c ON c.id=m.conversation_id JOIN leads l ON l.id=c.lead_id "
                "WHERE m.conversation_id=? AND m.direcao='inbound' ORDER BY m.indice",
                (conversation_id,),
            ).fetchall()
            return tuple(
                InboundMessage(
                    row[0], row[1], row[2], cast(MessageType, row[3]), row[4], row[5], row[6]
                )
                for row in rows
            )
