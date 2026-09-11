from __future__ import annotations

import logging
import re
from copy import copy
from hashlib import sha256

_CPF = re.compile(r"(?<!\w)(?:[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|[0-9]{11})(?!\w)")
_EMAIL = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
_PHONE = re.compile(
    r"(?<!\w)(?:\+55\s*(?:\([1-9][0-9]\)\s*|[1-9][0-9]\s*)[0-9]{4,5}[- ]?[0-9]{4}"
    r"|\([1-9][0-9]\)\s*[0-9]{4,5}[- ]?[0-9]{4}"
    r"|[1-9][0-9][ -][0-9]{4,5}[- ]?[0-9]{4}"
    r"|(?:[1-9][0-9])?[0-9]{4,5}-[0-9]{4})(?!\w)"
)
# Bare eleven-digit strings are ambiguous: require an explicit telephone label.
_LABELED_PHONE = re.compile(
    r"(\b(?:telefone|tel|celular|whatsapp|fone)\b\s*[:=]?\s*)[1-9][0-9]{9,10}(?!\w)",
    re.IGNORECASE,
)
_PLATE = re.compile(r"(?<!\w)[a-z]{3}-?[0-9][a-z0-9][0-9]{2}(?!\w)", re.IGNORECASE)
_CEP = re.compile(r"(?<!\w)[0-9]{5}-?[0-9]{3}(?!\w)")


def _valid_cpf(candidate: str) -> str | None:
    digits = re.sub(r"\D", "", candidate)
    if len(set(digits)) == 1:
        return None
    for size in (9, 10):
        total = sum(int(digits[index]) * (size + 1 - index) for index in range(size))
        check = (total * 10 % 11) % 10
        if check != int(digits[size]):
            return None
    return digits


class PrivacyRedactor:
    """Redact supported Brazilian PII before storage or model consumption."""

    def redact(self, text: str) -> str:
        text = _EMAIL.sub("[EMAIL]", text)
        text = _CPF.sub(lambda match: "[CPF]" if _valid_cpf(match[0]) else match[0], text)
        text = _PHONE.sub("[TELEFONE]", text)
        text = _LABELED_PHONE.sub(r"\1[TELEFONE]", text)
        text = _PLATE.sub("[PLACA]", text)
        return _CEP.sub("[CEP]", text)

    def cpf_hash(self, text: str) -> str | None:
        for match in _CPF.finditer(text):
            if digits := _valid_cpf(match[0]):
                return sha256(digits.encode("ascii")).hexdigest()
        return None


class RedactingFormatter(logging.Formatter):
    """Redact after the wrapped formatter renders args, exceptions and stack info."""

    def __init__(
        self,
        formatter: logging.Formatter | None = None,
        redactor: PrivacyRedactor | None = None,
    ) -> None:
        super().__init__()
        self._formatter = formatter or logging.Formatter()
        self._redactor = redactor or PrivacyRedactor()

    def format(self, record: logging.LogRecord) -> str:
        # A formatter caches exception text; keep that cache off the shared record.
        rendered = self._formatter.format(copy(record))
        return self._redactor.redact(rendered)


def install_redacting_logging(
    logger: logging.Logger, redactor: PrivacyRedactor | None = None
) -> None:
    """Wrap every existing handler on logger, preserving its chosen format.

    Wiring must call this after adding handlers, including the root logger's
    handlers when children propagate. Newly added handlers need installation too.
    """
    for handler in logger.handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(handler.formatter, redactor))
