from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from application.llm import LLMRequest, LLMResponse, LLMRole
from infrastructure.llm.recording import LLMFixtureMissing, RecordedLLMClient


def request():
    return LLMRequest("conversation", LLMRole.EXTRACTOR, "extract", "Tenho 30 anos", {}, 2.5)


@pytest.mark.asyncio
async def test_record_then_replay_never_calls_network_and_preserves_usage(tmp_path):
    response = LLMResponse("{}", "model-test", 30, 10, Decimal("0.000028"), 123.4)
    leaf = AsyncMock(complete=AsyncMock(return_value=response))
    models = {LLMRole.EXTRACTOR: "model-test"}
    recorder = RecordedLLMClient(leaf, tmp_path, "record", models)
    assert await recorder.complete(request()) == response
    replay = RecordedLLMClient(None, tmp_path, "replay", models)
    assert await replay.complete(request()) == response
    leaf.complete.assert_awaited_once()
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert "Tenho" not in files[0].read_text()


@pytest.mark.asyncio
async def test_missing_fixture_fails_clearly_without_fallback(tmp_path):
    leaf = AsyncMock()
    client = RecordedLLMClient(leaf, tmp_path, "replay", {LLMRole.EXTRACTOR: "model"})
    with pytest.raises(LLMFixtureMissing, match="Fixture LLM ausente"):
        await client.complete(request())
    leaf.complete.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_identical_calls_keep_individual_captures(tmp_path):
    first = LLMResponse("{}", "model", 2, 3, Decimal("0.1"), 10)
    second = LLMResponse("{}", "model", 4, 5, Decimal("0.2"), 20)
    leaf = AsyncMock(complete=AsyncMock(side_effect=[first, second]))
    models = {LLMRole.EXTRACTOR: "model"}
    record = RecordedLLMClient(leaf, tmp_path, "record", models)
    assert [await record.complete(request()), await record.complete(request())] == [first, second]
    replay = RecordedLLMClient(None, tmp_path, "replay", models)
    assert [await replay.complete(request()), await replay.complete(request())] == [first, second]
    assert len(list(tmp_path.glob("*.json"))) == 2


@pytest.mark.asyncio
async def test_configuration_changes_invalidate_capture(tmp_path):
    leaf = AsyncMock(complete=AsyncMock(return_value=LLMResponse("{}", "model", 2, 3, None, 1)))
    models = {LLMRole.EXTRACTOR: "model"}
    await RecordedLLMClient(leaf, tmp_path, "record", models, settings={"timeout": 2}).complete(
        request()
    )
    with pytest.raises(LLMFixtureMissing):
        await RecordedLLMClient(None, tmp_path, "replay", models, settings={"timeout": 3}).complete(
            request()
        )


@pytest.mark.asyncio
async def test_recorded_failure_replays_without_network(tmp_path):
    from application.llm import LLMUnavailable

    leaf = AsyncMock(complete=AsyncMock(side_effect=LLMUnavailable()))
    models = {LLMRole.EXTRACTOR: "model"}
    with pytest.raises(LLMUnavailable):
        await RecordedLLMClient(leaf, tmp_path, "record", models).complete(request())
    with pytest.raises(LLMUnavailable):
        await RecordedLLMClient(None, tmp_path, "replay", models).complete(request())
    leaf.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_corrupt_fixture_fails_clearly(tmp_path):
    from infrastructure.llm.recording import LLMFixtureInvalid

    leaf = AsyncMock(complete=AsyncMock(return_value=LLMResponse("{}", "model", 2, 3, None, 1)))
    models = {LLMRole.EXTRACTOR: "model"}
    await RecordedLLMClient(leaf, tmp_path, "record", models).complete(request())
    next(tmp_path.glob("*.json")).write_text("not JSON")
    with pytest.raises(LLMFixtureInvalid, match="Fixture LLM inválida"):
        await RecordedLLMClient(None, tmp_path, "replay", models).complete(request())


@pytest.mark.asyncio
async def test_budget_timeout_is_recorded_for_offline_replay(tmp_path):
    import asyncio
    from dataclasses import replace

    from application.llm import LLMUnavailable
    from infrastructure.llm.budget import BudgetedLLMClient

    class Pending:
        async def complete(self, request):
            await asyncio.Event().wait()

    models = {LLMRole.EXTRACTOR: "model"}
    req = replace(request(), budget=0)
    recording = RecordedLLMClient(Pending(), tmp_path, "record", models)
    with pytest.raises(LLMUnavailable):
        await BudgetedLLMClient(recording).complete(req)
    with pytest.raises(LLMUnavailable):
        await RecordedLLMClient(None, tmp_path, "replay", models).complete(req)


@pytest.mark.asyncio
async def test_external_cancellation_does_not_create_false_unavailable_capture(tmp_path):
    import asyncio

    from infrastructure.llm.budget import BudgetedLLMClient

    entered = asyncio.Event()

    class Pending:
        async def complete(self, request):
            entered.set()
            await asyncio.Event().wait()

    client = BudgetedLLMClient(
        RecordedLLMClient(Pending(), tmp_path, "record", {LLMRole.EXTRACTOR: "model"})
    )
    task = asyncio.create_task(client.complete(request()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.asyncio
async def test_failure_latency_survives_record_replay(tmp_path):
    from application.llm import LLMUnavailable

    class Clock:
        value = 0.0

        def monotonic(self):
            return self.value

    clock = Clock()

    class Failing:
        async def complete(self, request):
            clock.value = 2.5
            raise LLMUnavailable()

    models = {LLMRole.EXTRACTOR: "model"}
    recording = RecordedLLMClient(Failing(), tmp_path, "record", models, clock=clock)
    with pytest.raises(LLMUnavailable) as live:
        await recording.complete(request())
    with pytest.raises(LLMUnavailable) as replay:
        await RecordedLLMClient(None, tmp_path, "replay", models).complete(request())
    assert live.value.latency_ms == replay.value.latency_ms == 2500
