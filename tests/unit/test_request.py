from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from domain.quote import QuoteContractError, QuoteRequest


def request(cep: str | None = "01310100") -> QuoteRequest:
    return QuoteRequest("completo", 30, 2026, cep, date(2026, 9, 15))


@pytest.mark.parametrize("cep", ["01310-100", "01310 100", "01310100", "1310100"])
def test_cep_normalizes_without_losing_zero(cep: str) -> None:
    assert request(cep).cep == "01310100"
    assert request(cep).to_payload()["cep"] == "01310100"


def test_missing_optionals_stay_absent() -> None:
    req = QuoteRequest("completo", 30, 2026)
    assert req.cep is None
    assert req.data_inicio is None
    assert req.to_payload() == {"plano_id": "completo", "idade": 30, "veiculo_ano": 2026}


def test_payload_serializes_start_date_and_preserves_cep() -> None:
    assert request().to_payload() == {
        "plano_id": "completo",
        "idade": 30,
        "veiculo_ano": 2026,
        "cep": "01310100",
        "data_inicio": "2026-09-15",
    }
    assert replace(request(), plano_id="premium").to_payload()["cep"] == "01310100"


@pytest.mark.parametrize("cep", ["", "123456", "123456789", "0131a100", "０１３１０１００"])
def test_invalid_cep_is_rejected_without_echoing_pii(cep: str) -> None:
    with pytest.raises(QuoteContractError) as caught:
        request(cep)
    if cep:
        assert cep not in str(caught.value)


def test_cep_is_immutable_and_hidden_from_repr() -> None:
    req = request()
    assert "01310100" not in repr(req)
    assert not hasattr(req, "__dict__")
    with pytest.raises(FrozenInstanceError):
        req.cep = None  # type: ignore[misc]


def test_normalized_formats_share_fingerprint() -> None:
    fingerprints = {
        request(cep).fingerprint(date(2026, 9, 11))
        for cep in ["01310-100", "01310 100", "01310100", "1310100"]
    }
    assert len(fingerprints) == 1


def test_plan_case_matches_api_and_shares_fingerprint() -> None:
    upper = replace(request(), plano_id="COMPLETO")
    assert upper.plano_id == "completo"
    assert upper.to_payload() == request().to_payload()
    assert upper.fingerprint(date(2026, 9, 11)) == request().fingerprint(date(2026, 9, 11))


def test_reference_day_changes_fingerprint() -> None:
    assert request().fingerprint(date(2026, 9, 11)) != request().fingerprint(date(2026, 9, 12))


@pytest.mark.parametrize(
    "changes",
    [
        {"plano_id": "premium"},
        {"idade": 31},
        {"veiculo_ano": 2025},
        {"cep": "07101000"},
        {"cep": None},
        {"data_inicio": date(2026, 9, 16)},
        {"data_inicio": None},
    ],
)
def test_every_slot_participates_in_fingerprint(changes: dict[str, object]) -> None:
    original = request()
    changed = replace(original, **changes)  # type: ignore[arg-type]
    assert original.fingerprint(date(2026, 9, 11)) != changed.fingerprint(date(2026, 9, 11))


def test_fingerprint_has_stable_serialization() -> None:
    # Fixed external vector also holds across processes and PYTHONHASHSEED values.
    assert request().fingerprint(date(2026, 9, 11)) == (
        "269589b150b8e059a0b03fb16b3ed78625f80594ae814e34277cfe6a11f480c1"
    )
