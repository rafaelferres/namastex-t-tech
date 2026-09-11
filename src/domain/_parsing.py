"""Validação de valores externos, sem reter ou reproduzir entradas em erros."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal


def mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Objeto inválido")
    return value


def sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Lista inválida")
    return value


def integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Inteiro inválido")
    return value


def nonnegative_integer(value: object) -> int:
    result = integer(value)
    if result < 0:
        raise ValueError("Inteiro negativo")
    return result


def string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Texto inválido")
    return value


def strings(value: object) -> tuple[str, ...]:
    return tuple(string(item) for item in sequence(value))


def money(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Valor monetário inválido")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Valor monetário inválido")
    return result
