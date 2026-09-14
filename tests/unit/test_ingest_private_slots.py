from __future__ import annotations

from agent.nodes.extract import capture_private_cep
from application.ingest import Ingestor
from infrastructure.privacy import PrivacyRedactor
from tests.unit.test_ingest import MemoryStore, message
from tests.virtual_time import virtual_time


def test_cep_reaches_private_consumer_but_never_message_storage_or_repr():
    with virtual_time() as clock:
        store, turns = MemoryStore(), []

        async def consume(turn):
            turns.append(turn)

        async def run():
            async with Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=clock,
                sleep=clock.sleep,
                capture_cep=capture_private_cep,
            ) as ingest:
                await ingest.ingest(message(body="CEP 01310-100"))
                assert not await ingest.ingest(message(body="CEP 99999-999"))
            assert turns[0].private_cep == "01310100"
            assert "01310100" not in repr(turns[0])
            assert "01310" not in store.saved["conv-a-0"].corpo

        clock.run(run())


def test_punctuated_cep_is_captured_privately_and_unmasked_phone_is_never_stored():
    with virtual_time() as clock:
        store, turns = MemoryStore(), []

        async def consume(turn):
            turns.append(turn)

        async def run():
            async with Ingestor(
                store,
                store,
                store,
                PrivacyRedactor(),
                consume,
                clock=clock,
                sleep=clock.sleep,
                capture_cep=capture_private_cep,
            ) as ingest:
                await ingest.ingest(message(body="Meu telefone é 11987654321, CEP 07.123-456"))
            assert turns[0].private_cep == "07123456"
            expected = "Meu telefone é [TELEFONE], CEP [CEP]"
            assert store.saved["conv-a-0"].corpo == expected
            assert turns[0].messages[0].corpo == expected

        clock.run(run())
