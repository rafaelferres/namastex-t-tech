"""Shared case loader and honest metrics: labels never reach the extractor."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from infrastructure.privacy import PrivacyRedactor
from interfaces.replay import envelopes_from_rows


@dataclass(frozen=True, slots=True)
class Case:
    conversation_id: str
    messages: tuple[str, ...]
    idade: int
    veiculo_texto: str
    reference_date: str
    outcome: str


@dataclass(frozen=True, slots=True)
class ExtractedSlots:
    idade: int | None = None
    veiculo_texto: str | None = None


class Extractor(Protocol):
    async def extract(self, messages: tuple[str, ...]) -> ExtractedSlots: ...


class NullExtractor:
    """Offline placeholder, deliberately reports zero rather than using labels."""

    async def extract(self, messages: tuple[str, ...]) -> ExtractedSlots:
        return ExtractedSlots()


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    total: int
    idade_correct: int
    veiculo_correct: int

    @property
    def idade_accuracy(self) -> float:
        return self.idade_correct / self.total

    @property
    def veiculo_accuracy(self) -> float:
        return self.veiculo_correct / self.total


async def evaluate(cases: Iterable[Case], extractor: Extractor) -> ExtractionReport:
    total = idade = veiculo = 0
    for case in cases:
        prediction = await extractor.extract(case.messages)
        total += 1
        idade += prediction.idade == case.idade
        veiculo += prediction.veiculo_texto == case.veiculo_texto
    if not total:
        raise ValueError("Conjunto de avaliação vazio")
    return ExtractionReport(total, idade, veiculo)


def cases_from_rows(rows: Iterable[Mapping[str, object]]) -> tuple[Case, ...]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["conversation_id"])].append(row)
    redactor = PrivacyRedactor()
    cases = []
    for conversation_id, group in sorted(grouped.items()):
        first = min(group, key=lambda row: int(str(row["message_index"])))
        reference_date = date.fromisoformat(str(first["timestamp"])[:10]).isoformat()
        cases.append(
            Case(
                conversation_id,
                tuple(redactor.redact(message.corpo) for message in envelopes_from_rows(group)),
                int(str(first["lead_idade_informada"])),
                str(first["veiculo_texto"]),
                reference_date,
                str(first["conversation_outcome"]),
            )
        )
    return tuple(cases)
