from __future__ import annotations

from infrastructure.quote.config import QuoteConfig


def test_calibration_uses_measured_fast_path_and_constant_jitter() -> None:
    config = QuoteConfig()
    assert config.hedge_delay == 0.1  # > 2 * measured p99 (44.73 ms).
    assert config.base_delay == config.max_delay == 0.02
