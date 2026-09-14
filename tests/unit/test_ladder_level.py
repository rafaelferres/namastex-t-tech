"""Nível da escada atingido num turno, lido do que foi persistido (tarefa 14)."""

from __future__ import annotations

from datetime import datetime

import pytest

from application.inspect_conversation import TurnReport, ladder_level
from application.tracing import QuoteAttempt
from application.turns import TurnEvent
from domain.handoff import HandoffDecision, HandoffReason
from interfaces.conversation_report import render_turn

NOW = datetime(2026, 9, 14, 10)


def attempt(
    tentativa: int, status: str = "unavailable", *, origem: str = "api", hedge: bool = False
) -> QuoteAttempt:
    return QuoteAttempt(
        "c:1", "c", "fp", tentativa, status, origem, 500, 20, hedge, False, None, NOW  # type: ignore[arg-type]
    )


def report(
    *attempts: QuoteAttempt,
    escalacao: HandoffDecision | None = None,
    etapas: tuple[TurnEvent, ...] = (),
) -> TurnReport:
    return TurnReport(
        "c:1", "Quero o Completo", (), ("quote",), "cotada", None, None, None, None,
        etapas, attempts, None, escalacao,
    )


QUOTED = attempt(0, "quoted")


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [
        ((), None),
        ((attempt(1, "quoted"), QUOTED), "N0 — chamada direta"),
        ((attempt(1), attempt(2, "quoted", hedge=True), QUOTED), "N0 — chamada direta, com hedge"),
        ((attempt(1), attempt(2, "quoted"), QUOTED), "N1 — retry (2 tentativas)"),
        (
            (attempt(1), attempt(2), attempt(3, "quoted", hedge=True), QUOTED),
            "N1 — retry (2 tentativas), com hedge",
        ),
        ((attempt(0, "quoted", origem="cache"),), "fora da escada — cache do dia"),
        (
            (attempt(0, "declined", origem="regra_local"),),
            "fora da escada — recusa local, sem rede",
        ),
    ],
)
def test_ladder_level_from_attempts(
    attempts: tuple[QuoteAttempt, ...], expected: str | None
) -> None:
    assert ladder_level(report(*attempts)) == expected


def test_exhausted_ladder_with_escalation_is_n2() -> None:
    decision = HandoffDecision(True, HandoffReason.COTACAO, None, False, None)
    failed = (attempt(1), attempt(2), attempt(3), attempt(0))
    assert ladder_level(report(*failed, escalacao=decision)) == "N2 — escalação com snapshot"


def test_trace_shows_ladder_and_guardrail() -> None:
    guardrail = TurnEvent("c:1", "c", "guardrail", "violacao", 0, "valor_monetario", NOW)
    text = "\n".join(
        render_turn(1, report(attempt(1), attempt(2, "quoted"), QUOTED, etapas=(guardrail,)))
    )
    assert "- Escada: N1 — retry (2 tentativas)" in text
    assert "- Guardrail: fala do modelo descartada (`valor_monetario`)" in text
    assert "Escada" not in "\n".join(render_turn(1, report()))
