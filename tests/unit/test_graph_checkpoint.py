from __future__ import annotations

import asyncio

import pytest

from agent.nodes.extract import ExtractionResult
from agent.schemas.slots import Slots
from infrastructure.persistence.checkpoint import open_checkpointer
from tests.fakes import FakeClock
from tests.unit.test_graph import build


@pytest.mark.asyncio
async def test_checkpoint_survives_restart_and_deduplicates(tmp_path, plans_payload, quote_payload):
    path = tmp_path / "agent.sqlite"
    clock = FakeClock()
    async with open_checkpointer(path) as saver:
        graph, extractor, _, _, _ = build(clock, plans_payload, quote_payload, checkpointer=saver)
        extractor.extract.return_value = ExtractionResult(
            Slots.model_validate(
                {"idade": {"valor": 30, "status": "informado", "proveniencia": "digitado"}}
            )
        )
        first = await graph.turn("c", "m1", "Tenho trinta anos")
        assert first["slots"]["idade"]["valor"] == 30
    async with open_checkpointer(path) as saver:
        graph, extractor, _, _, _ = build(clock, plans_payload, quote_payload, checkpointer=saver)
        assert (await graph.turn("c", "m1", "reentrega"))["slots"] == first["slots"]
        extractor.extract.assert_not_called()
        await graph.turn("c", "m2", "Veículo novo")
        previous = extractor.extract.call_args.args[1]
        assert previous.idade.valor == 30


@pytest.mark.asyncio
async def test_same_conversation_turns_never_overlap(tmp_path, plans_payload, quote_payload):
    async with open_checkpointer(tmp_path / "agent.sqlite") as saver:
        graph, extractor, _, _, _ = build(
            FakeClock(), plans_payload, quote_payload, checkpointer=saver
        )
        entered, release = asyncio.Event(), asyncio.Event()
        active = 0
        maximum = 0

        async def extract(*args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            entered.set()
            await release.wait()
            active -= 1
            return ExtractionResult(Slots())

        extractor.extract.side_effect = extract
        first = asyncio.create_task(graph.turn("c", "m1", "Olá"))
        await entered.wait()
        second = asyncio.create_task(graph.turn("c", "m2", "Outra mensagem"))
        release.set()
        await asyncio.gather(first, second)
        assert maximum == 1
