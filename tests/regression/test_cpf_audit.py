from __future__ import annotations

import re

import pytest

from infrastructure.privacy import PrivacyRedactor
from interfaces.replay import dataset_path, read_rows


def independent_checksum(value: str) -> bool:
    digits = [int(character) for character in value if character.isdecimal()]
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        weights = zip(digits[:size], range(size + 1, 1, -1), strict=True)
        remainder = sum(a * b for a, b in weights) % 11
        if digits[size] != (0 if remainder < 2 else 11 - remainder):
            return False
    return True


@pytest.mark.slow
def test_all_labeled_cpf_spans_are_valid_and_redacted_without_false_positives() -> None:
    path = dataset_path()
    if not path.is_file():
        pytest.skip("Corpus local ausente: auditoria CPF não executada; use AUTOSEGURO_DATASET")
    # Labels derive from upstream generator's 'CPF {cpf()}' template, independently
    # of the production detector and its checksum implementation.
    label = re.compile(r"\bcpf\s+([^,\s]+)", re.IGNORECASE)
    redactor = PrivacyRedactor()
    labeled = invalid = false_positive = false_negative = 0
    for row in read_rows(path):
        text = str(row["message_body"])
        spans = [match[1] for match in label.finditer(text) if any(c.isdigit() for c in match[1])]
        labeled += len(spans)
        invalid += sum(not independent_checksum(span) for span in spans)
        rendered = redactor.redact(text)
        false_negative += sum(span in rendered for span in spans)
        false_positive += max(0, rendered.count("[CPF]") - len(spans))
    # Assert aggregate counts only: failures must never render raw rows or spans.
    assert (labeled, invalid, false_positive, false_negative) == (2500, 0, 0, 0)
