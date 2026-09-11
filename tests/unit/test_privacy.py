from __future__ import annotations

import logging
from hashlib import sha256
from io import StringIO

import pytest

from infrastructure.privacy import PrivacyRedactor


@pytest.mark.parametrize("label", ["CPF", "Cpf", "cpf", "documento"])
@pytest.mark.parametrize("cpf", ["529.982.247-25", "52998224725"])
def test_valid_cpf_redacted_anywhere_and_hashed_canonically(label: str, cpf: str) -> None:
    redactor = PrivacyRedactor()
    text = f"Tenho um carro; {label}: {cpf}; quero cotar"
    assert redactor.redact(text) == f"Tenho um carro; {label}: [CPF]; quero cotar"
    assert redactor.cpf_hash(text) == sha256(b"52998224725").hexdigest()


@pytest.mark.parametrize("cpf", ["52998224724", "52998224715", "11111111111", "00000000000"])
def test_invalid_cpf_is_not_identified_as_cpf_or_arbitrary_phone(cpf: str) -> None:
    redactor = PrivacyRedactor()
    assert redactor.redact(cpf) == cpf
    assert redactor.cpf_hash(cpf) is None


def test_cpf_search_skips_invalid_candidate_and_preserves_other_numbers() -> None:
    redactor = PrivacyRedactor()
    text = "Ano 2025, idade 35, CPF 11111111111, depois 529.982.247-25"
    assert redactor.redact(text) == "Ano 2025, idade 35, CPF 11111111111, depois [CPF]"
    assert redactor.cpf_hash(text) == sha256(b"52998224725").hexdigest()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Email TESTE+seguro@example.test", "Email [EMAIL]"),
        ("Ligue (11) 99999-8888", "Ligue [TELEFONE]"),
        ("Ligue 11 99999-8888", "Ligue [TELEFONE]"),
        ("Ligue 1199999-8888", "Ligue [TELEFONE]"),
        ("Ligue 99999-8888 ou 3333-4444", "Ligue [TELEFONE] ou [TELEFONE]"),
        ("Contato +55 (11) 99999-8888", "Contato [TELEFONE]"),
        ("Contato +5511999998888", "Contato [TELEFONE]"),
        ("Telefone: 11999998888", "Telefone: [TELEFONE]"),
        ("WHATSAPP 1133334444", "WHATSAPP [TELEFONE]"),
        ("Placa abc-1234 ou ABC1D23", "Placa [PLACA] ou [PLACA]"),
        ("CEP: 07123-456 e 07123456", "CEP: [CEP] e [CEP]"),
    ],
)
def test_other_supported_pii(text: str, expected: str) -> None:
    assert PrivacyRedactor().redact(text) == expected


def test_redacts_shuffled_fields_and_is_idempotent() -> None:
    redactor = PrivacyRedactor()
    text = "abc1d23, 07123-456, teste@example.test; Cpf 52998224725; (11) 99999-8888"
    expected = "[PLACA], [CEP], [EMAIL]; Cpf [CPF]; [TELEFONE]"
    assert redactor.redact(text) == expected
    assert redactor.redact(expected) == expected


def test_logging_redacts_arguments_traceback_and_custom_format_in_all_handlers() -> None:
    from infrastructure.privacy import install_redacting_logging

    logger = logging.Logger("privacy-test")
    outputs = [StringIO(), StringIO()]
    for output in outputs:
        handler = logging.StreamHandler(output)
        handler.setFormatter(logging.Formatter("{levelname}|{message}", style="{"))
        logger.addHandler(handler)
    install_redacting_logging(logger)
    install_redacting_logging(logger)
    try:
        raise ValueError("teste@example.test; 529.982.247-25; (11) 99999-8888")
    except ValueError:
        logger.exception("Placa %s, CEP %s", "ABC1D23", "07123-456")
    for output in outputs:
        rendered = output.getvalue()
        assert rendered.startswith("ERROR|Placa [PLACA], CEP [CEP]")
        assert "Traceback (most recent call last)" in rendered
        assert "ValueError: [EMAIL]; [CPF]; [TELEFONE]" in rendered
        for raw in ("teste@example.test", "529.982.247-25", "99999-8888", "ABC1D23", "07123-456"):
            assert raw not in rendered
