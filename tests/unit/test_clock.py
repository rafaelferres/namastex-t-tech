from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from application import ports
from application.ports import Clock, SystemClock


def test_system_clock_uses_one_local_calendar_and_monotonic_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Boundary seam only: never read the real system clock in this test.
    fixed = datetime(2026, 9, 11, 23, 59, tzinfo=timezone(timedelta(hours=-3)))

    class FixedDatetime:
        @classmethod
        def now(cls) -> datetime:
            return fixed

    monkeypatch.setattr(ports, "datetime", FixedDatetime)
    monkeypatch.setattr(ports, "monotonic", lambda: 12.5)
    clock: Clock = SystemClock()
    assert clock.now() == fixed
    assert clock.today() == fixed.date()
    assert clock.monotonic() == 12.5
