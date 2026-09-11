from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class FakeClock:
    instant: datetime = datetime(2026, 9, 11)
    elapsed: float = 0.0

    def today(self) -> date:
        return self.instant.date()

    def now(self) -> datetime:
        return self.instant

    def monotonic(self) -> float:
        return self.elapsed
