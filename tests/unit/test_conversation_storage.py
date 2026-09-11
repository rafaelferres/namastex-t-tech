from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain.messages import InboundMessage
from infrastructure.persistence.connection import apply_schema, connect

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def message() -> InboundMessage:
    return InboundMessage("whatsapp", "conv-a", "5511999999999", "text", "[CPF]", "msg-1", 1)


@pytest.mark.asyncio
async def test_identity_hash_dedup_order_and_objections() -> None:
    from infrastructure.persistence.conversations import SQLiteConversations

    conn = connect(":memory:")
    try:
        store = SQLiteConversations(conn)
        msg = message()
        first = await store.ensure(msg, "a" * 64, NOW)
        second = await store.ensure(replace(msg, conversation_id="conv-b"), None, NOW)
        other = await store.ensure(
            replace(msg, conversation_id="conv-c", channel_user_id="other"), None, NOW
        )
        assert first.lead_id == second.lead_id != other.lead_id
        assert await store.get("missing") is None
        assert await store.get(first.id) == first
        lead = await store.lead(msg.channel, msg.channel_user_id)
        assert lead is not None and lead.cpf_hash == "a" * 64
        assert lead.channel_user_id != msg.channel_user_id
        assert await store.lead("cli", msg.channel_user_id) is None
        assert await store.add(msg, NOW)
        assert not await store.add(msg, NOW)
        assert not await store.add(replace(msg, conversation_id="conv-b"), NOW)
        with pytest.raises(sqlite3.IntegrityError):
            await store.add(
                replace(msg, conversation_id="missing", provider_message_id="orphan"), NOW
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE conversations SET status='invalido'")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE leads SET cpf_hash='raw-cpf'")
        conn.rollback()
        assert await store.add(
            replace(
                msg, indice=0, provider_message_id="msg-0", tipo="audio", media_ref="private.wav"
            ),
            NOW,
        )
        rows = await store.messages(first.id)
        assert [row.indice for row in rows] == [0, 1]
        assert rows[0].media_ref is None
        assert await store.add_price_objection(first.id) == 1
        assert await store.add_price_objection(first.id) == 2
        assert (await store.get(first.id)).objecoes_preco == 2
        assert (await store.get(second.id)).objecoes_preco == 0
        dump = "\n".join(conn.iterdump())
        assert msg.channel_user_id not in dump and "private.wav" not in dump
        with pytest.raises(sqlite3.IntegrityError):
            await store.add(replace(msg, provider_message_id="collision"), NOW)
        with pytest.raises(ValueError):
            await store.ensure(replace(msg, channel_user_id="changed"), None, NOW)
        with pytest.raises(ValueError):
            await store.ensure(msg, "raw-cpf", NOW)
        with pytest.raises(ValueError):
            await store.ensure(msg, "b" * 64, NOW)
    finally:
        conn.close()


def test_legacy_migration_preserves_trace_and_enforces_foreign_key(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    schema = Path("src/infrastructure/persistence/schema.sql").read_text()
    start = schema.index("CREATE TABLE IF NOT EXISTS quote_attempts")
    old = schema[start:].split("CREATE INDEX")[0].replace(" REFERENCES conversations(id)", "")
    conn.executescript(old)
    conn.execute(
        "INSERT INTO quote_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "id",
            "trace",
            "legacy-conv",
            "fingerprint",
            1,
            "quoted",
            "api",
            200,
            2,
            0,
            0,
            None,
            NOW.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    conn = connect(path)
    try:
        apply_schema(conn)
        assert conn.execute("SELECT count(*) FROM quote_attempts").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            conn.execute("PRAGMA foreign_key_list(quote_attempts)").fetchone()[2] == "conversations"
        )
        assert (
            conn.execute(
                "SELECT channel FROM leads JOIN conversations ON lead_id=leads.id "
                "WHERE conversations.id='legacy-conv'"
            ).fetchone()[0]
            == "legacy"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE quote_attempts SET conversation_id='missing'")
    finally:
        conn.close()
