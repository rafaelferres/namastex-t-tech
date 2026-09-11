from __future__ import annotations

from domain.quote import Declined, QuoteRequest
from infrastructure.quote.trace import ApplicationTrace
from infrastructure.tracing.recorder import BufferedAttemptRecorder
from tests.unit.test_trace import correlation
from tests.virtual_time import ScriptedProvider, Step, virtual_time


def test_logical_boundary_persists_without_manual_flush() -> None:
    with virtual_time() as timeline:
        saved = []

        async def write(event) -> None:
            await timeline.sleep(0.006)
            saved.append(event)

        recorder = BufferedAttemptRecorder(write)
        app = ApplicationTrace(
            ScriptedProvider(timeline, Step(Declined("Recusado"))),
            recorder,
            correlation(),
            timeline,
        )

        async def run() -> None:
            await app.quote(QuoteRequest("completo", 30, 2026))
            assert len(saved) == 1
            assert saved[0].tentativa == 0

        timeline.run(run())
