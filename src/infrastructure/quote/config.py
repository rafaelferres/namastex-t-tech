from __future__ import annotations

from dataclasses import dataclass

PRODUCTION_QUOTE_BUDGET = 3.5


@dataclass(frozen=True, slots=True)
class QuoteConfig:
    budget: float = PRODUCTION_QUOTE_BUDGET
    timeout: float = 2.0
    hedge_delay: float = 1.5
    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 0.4
    contract_threshold: int = 3
