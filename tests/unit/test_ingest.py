from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from application.ingest import Ingestor
from domain.messages import InboundMessage
from infrastructure.privacy import PrivacyRedactor
from tests.virtual_time import virtual_time


class MemoryStore:
    def __init__(self):
        self.saved = {}
        self.counts = {}
        self.hashes = []

    async def ensure(self, message, cpf_hash, now):
        self.hashes.append(cpf_hash)
        return SimpleNamespace(id=message.conversation_id, lead_id="opaque-lead")

    async def add(self, message, now):
        if message.provider_message_id in self.saved:
            return False
        self.saved[message.provider_message_id] = message
        return True

    async def add_price_objection(self, conversation_id):
        self.counts[conversation_id] = self.counts.get(conversation_id, 0) + 1
        return self.counts[conversation_id]

    async def get(self, conversation_id):
        return SimpleNamespace(objecoes_preco=self.counts.get(conversation_id, 0))


def message(index=0, conversation="conv-a", body="Olá"):
    return InboundMessage(
        "cli", conversation, "user-a", "text", body, f"{conversation}-{index}", index
    )


def test_six_fragments_are_one_redacted_turn_and_duplicate_is_ignored():
    with virtual_time() as time:
        store = MemoryStore()
        turns = []

        async def consume(turn):
            turns.append(turn)

        async def run():
            async with Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=time,
                sleep=time.sleep,
                window=0.5,
            ) as ingest:
                for index in range(6):
                    assert await ingest.ingest(message(index, body="CPF 529.982.247-25"))
                assert not await ingest.ingest(message(0))
            assert len(turns) == 1
            assert [m.indice for m in turns[0].messages] == list(range(6))
            assert all("529" not in m.corpo for m in store.saved.values())
            assert all(value and len(value) == 64 for value in store.hashes[:-1])
            assert turns[0].messages[0].channel_user_id == "opaque-lead"

        time.run(run())


def test_failed_consumer_can_retry_without_duplicate_message_or_objection():
    with virtual_time() as time:
        store = MemoryStore()
        turns = []

        async def consume(turn):
            turns.append(turn)
            if len(turns) == 1:
                raise RuntimeError("consumer failed")

        async def run():
            ingest = Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=time,
                sleep=time.sleep,
                window=0.1,
            )
            await ingest.ingest(message(body="Está caro"))
            with pytest.raises(RuntimeError, match="consumer failed"):
                await ingest.wait_idle()
            await ingest.wait_idle()
            assert len(turns) == 2 and turns[0] == turns[1]
            assert turns[1].objecoes_preco == 1

        time.run(run())


def test_price_objection_can_span_fragments():
    with virtual_time() as time:
        store = MemoryStore()
        turns = []

        async def consume(turn):
            turns.append(turn)

        async def run():
            async with Ingestor(
                store, store, store, PrivacyRedactor(), consume, clock=time, sleep=time.sleep
            ) as ingest:
                await ingest.ingest(message(body="preço"))
                await ingest.ingest(message(1, body="alto"))
            assert turns[0].objecoes_preco == 1

        time.run(run())


def test_cancelling_idle_wait_drains_accepted_turn_before_returning():
    with virtual_time() as time:
        store = MemoryStore()
        turns = []

        async def consume(turn):
            await time.sleep(2)
            turns.append(turn)

        async def run():
            ingest = Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=time,
                sleep=time.sleep,
                window=0.1,
            )
            await ingest.ingest(message())
            barrier = asyncio.create_task(ingest.wait_idle())
            await time.sleep(0.2)
            barrier.cancel()
            with pytest.raises(asyncio.CancelledError):
                await barrier
            assert len(turns) == 1

        time.run(run())


def test_consumer_can_enqueue_next_turn_without_deadlock():
    with virtual_time() as time:
        store = MemoryStore()
        turns = []

        async def consume(turn):
            turns.append(turn)
            if len(turns) == 1:
                await ingest.ingest(message(1))

        ingest = Ingestor(
            store,
            store,
            store,
            PrivacyRedactor(),
            consume,
            clock=time,
            sleep=time.sleep,
            window=0.1,
        )

        async def run():
            await ingest.ingest(message())
            await ingest.wait_idle()
            assert len(turns) == 2

        time.run(run())


def test_cancelling_ingress_after_commit_still_enqueues_the_message():
    with virtual_time() as time:

        class CommittedStore(MemoryStore):
            async def add(self, incoming, now):
                accepted = await super().add(incoming, now)
                await time.sleep(1)
                return accepted

        store = CommittedStore()
        turns = []

        async def consume(turn):
            turns.append(turn)

        async def run():
            ingest = Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=time,
                sleep=time.sleep,
                window=0.1,
            )
            ingress = asyncio.create_task(ingest.ingest(message()))
            await time.sleep(0.1)
            assert store.saved
            ingress.cancel()
            with pytest.raises(asyncio.CancelledError):
                await ingress
            await ingest.wait_idle()
            assert len(turns) == 1
            assert not await ingest.ingest(message())

        time.run(run())


def test_conversations_are_isolated_and_each_consumer_is_serial():
    with virtual_time() as time:
        store = MemoryStore()
        active = set()
        seen = []

        async def consume(turn):
            assert turn.conversation_id not in active
            active.add(turn.conversation_id)
            await time.sleep(2)
            seen.append(turn)
            active.remove(turn.conversation_id)

        async def run():
            async with Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=time,
                sleep=time.sleep,
                window=0.1,
            ) as ingest:
                await ingest.ingest(message())
                await ingest.ingest(message(conversation="conv-b"))
                await time.sleep(0.2)
                await asyncio.gather(ingest.ingest(message(1)), ingest.ingest(message(2)))
            assert len(seen) == 3
            assert all(len({m.conversation_id for m in t.messages}) == 1 for t in seen)

        time.run(run())


def test_price_floor_counts_turns_not_repeated_fragments_or_duplicates():
    with virtual_time() as time:
        store = MemoryStore()
        turns = []

        async def consume(turn):
            turns.append(turn)

        async def run():
            async with Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=time,
                sleep=time.sleep,
                window=0.1,
            ) as ingest:
                for index in range(3):
                    await ingest.ingest(message(index, body="Está caro"))
                    await ingest.wait_idle()
                await ingest.ingest(message(3, body="Não está caro"))
                await ingest.ingest(replace(message(4), tipo="image", media_ref="private-file"))
            assert [t.objecoes_preco for t in turns] == [1, 2, 3, 3]
            assert turns[-1].messages[-1].media_status == "nao_resolvido"
            assert turns[-1].messages[-1].media_ref is None

        time.run(run())
