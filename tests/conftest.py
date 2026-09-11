from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def plans_payload() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "fixtures/plans.json").read_text())


@pytest.fixture(scope="session")
def quote_payload() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "fixtures/quote.json").read_text())
