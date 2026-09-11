from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

from domain.handoff import ConversationContext, HandoffDecision, HandoffPolicy
from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.persistence.delivery import SQLiteDelivery
from tests.unit.test_conversation_storage import message


def _decision() -> HandoffDecision:
    return HandoffPolicy().evaluate(ConversationContext(pede_humano=True))


@dataclass
class MutableClock:
    instant: datetime

    def today(self) -> date:
        return self.instant.date()

    def now(self) -> datetime:
        return self.instant

    def monotonic(self) -> float:
        return 0.0


class RecordingSink:
    def __init__(self, calls: list[tuple[str, str]], *, fail_once: bool = False) -> None:
        self._calls = calls
        self._fail_once = fail_once

    async def emit(
        self,
        decision: HandoffDecision,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> None:
        assert decision.escalar
        self._calls.append((conversation_id, idempotency_key))
        if self._fail_once:
            self._fail_once = False
            raise RuntimeError("CPF 529.982.247-25 e pessoa@example.com")


@pytest.mark.asyncio
async def test_enqueue_handoff_is_atomic_and_idempotent() -> None:
    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, 12, tzinfo=UTC)
        await SQLiteConversations(conn).ensure(message(), None, now)
        store = SQLiteDelivery(conn)

        first = await store.enqueue_handoff("conv-a", _decision(), now, identifier="trace-1")
        second = await store.enqueue_handoff("conv-a", _decision(), now, identifier="trace-1")

        assert first == second == "trace-1"
        assert conn.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
        assert conn.execute(
            "SELECT id, destino, status, tentativas, erro, proxima_tentativa_em "
            "FROM outbound_messages ORDER BY rowid"
        ).fetchall() == [
            ("trace-1:lead", "lead", "pendente", 0, None, now.isoformat()),
            (
                "trace-1:webhook_vendas",
                "webhook_vendas",
                "pendente",
                0,
                None,
                now.isoformat(),
            ),
            ("trace-1:api_fila", "api_fila", "pendente", 0, None, now.isoformat()),
        ]
        assert await store.handoff("trace-1") == _decision()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_enqueue_handoff_rolls_back_decision_and_all_effects() -> None:
    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, 12, tzinfo=UTC)
        await SQLiteConversations(conn).ensure(message(), None, now)
        conn.execute(
            "CREATE TRIGGER fail_queue BEFORE INSERT ON outbound_messages "
            "WHEN NEW.destino = 'api_fila' BEGIN SELECT RAISE(ABORT, 'controlled'); END"
        )
        store = SQLiteDelivery(conn)

        with pytest.raises(sqlite3.IntegrityError, match="controlled"):
            await store.enqueue_handoff("conv-a", _decision(), now, identifier="trace-atomic")

        assert conn.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM outbound_messages").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_dispatcher_orders_effects_and_retries_failures_independently() -> None:
    from application.outbox import HandoffDispatcher

    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, 12, tzinfo=UTC)
        clock = MutableClock(now)
        await SQLiteConversations(conn).ensure(message(), None, now)
        store = SQLiteDelivery(conn)
        await store.enqueue_handoff("conv-a", _decision(), now, identifier="trace-2")
        calls: list[tuple[str, str]] = []
        dispatcher = HandoffDispatcher(
            store,
            {
                "lead": RecordingSink(calls, fail_once=True),
                "webhook_vendas": RecordingSink(calls),
                "api_fila": RecordingSink(calls),
            },
            clock=clock,
            retry_delays=(1.0, 5.0),
        )

        assert await dispatcher.drain_due() == 3
        assert calls == [
            ("conv-a", "trace-2:lead"),
            ("conv-a", "trace-2:webhook_vendas"),
            ("conv-a", "trace-2:api_fila"),
        ]
        assert conn.execute(
            "SELECT destino, status, tentativas, erro, proxima_tentativa_em "
            "FROM outbound_messages ORDER BY rowid"
        ).fetchall() == [
            ("lead", "falhou", 1, "RuntimeError", (now + timedelta(seconds=1)).isoformat()),
            ("webhook_vendas", "entregue", 1, None, None),
            ("api_fila", "entregue", 1, None, None),
        ]
        dump = "\n".join(conn.iterdump())
        assert "529.982.247-25" not in dump
        assert "pessoa@example.com" not in dump

        assert await dispatcher.drain_due() == 0
        clock.instant += timedelta(seconds=1)
        assert await dispatcher.drain_due() == 1
        assert calls[-1] == ("conv-a", "trace-2:lead")
        assert conn.execute(
            "SELECT status, tentativas, erro, proxima_tentativa_em "
            "FROM outbound_messages WHERE destino='lead'"
        ).fetchone() == ("entregue", 2, None, None)
        assert await dispatcher.drain_due() == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_dispatcher_resumes_pending_effects_after_reopening_database(tmp_path) -> None:
    from application.outbox import HandoffDispatcher

    path = tmp_path / "delivery.sqlite3"
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    first = connect(path)
    await SQLiteConversations(first).ensure(message(), None, now)
    await SQLiteDelivery(first).enqueue_handoff(
        "conv-a", _decision(), now, identifier="trace-restart"
    )
    first.close()

    second = connect(path)
    try:
        calls: list[tuple[str, str]] = []
        store = SQLiteDelivery(second)
        dispatcher = HandoffDispatcher(
            store,
            {
                "lead": RecordingSink(calls),
                "webhook_vendas": RecordingSink(calls),
                "api_fila": RecordingSink(calls),
            },
            clock=MutableClock(now),
        )

        assert await dispatcher.drain_due() == 3
        assert [key for _, key in calls] == [
            "trace-restart:lead",
            "trace-restart:webhook_vendas",
            "trace-restart:api_fila",
        ]
    finally:
        second.close()


@pytest.mark.asyncio
async def test_dispatcher_context_polls_with_injected_sleep() -> None:
    from application.outbox import HandoffDispatcher

    conn = connect(":memory:")
    try:
        now = datetime(2026, 9, 11, 12, tzinfo=UTC)
        await SQLiteConversations(conn).ensure(message(), None, now)
        store = SQLiteDelivery(conn)
        await store.enqueue_handoff("conv-a", _decision(), now, identifier="trace-poll")
        calls: list[tuple[str, str]] = []
        sleep_started = asyncio.Event()
        sleep_delays: list[float] = []

        async def sleep(delay: float) -> None:
            sleep_delays.append(delay)
            sleep_started.set()
            await asyncio.Future()

        dispatcher = HandoffDispatcher(
            store,
            {
                "lead": RecordingSink(calls),
                "webhook_vendas": RecordingSink(calls),
                "api_fila": RecordingSink(calls),
            },
            clock=MutableClock(now),
            sleep=sleep,
            poll_interval=0.25,
        )
        async with dispatcher:
            await asyncio.wait_for(sleep_started.wait(), timeout=1)

        assert len(calls) == 3
        assert sleep_delays == [0.25]
    finally:
        conn.close()


def test_schema_migrates_legacy_outbox_and_adds_turn_events(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE outbound_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            destino TEXT NOT NULL,
            status TEXT NOT NULL,
            tentativas INTEGER NOT NULL DEFAULT 0,
            criado_em TEXT NOT NULL,
            entregue_em TEXT
        );
        """
    )
    legacy.close()

    migrated = connect(path)
    try:
        columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(outbound_messages)").fetchall()
        }
        assert {"erro", "proxima_tentativa_em"} <= columns
        assert migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='turn_events'"
        ).fetchone() == ("turn_events",)
        assert migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_turn_events_trace'"
        ).fetchone() == ("idx_turn_events_trace",)
    finally:
        migrated.close()
