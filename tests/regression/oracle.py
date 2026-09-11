"""Eligibility labels use catalog rules and the conversation's reference date."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from domain.acceptance import AcceptanceRules
from domain.quote import Declined, QuoteRequest
from tests.golden.harness import Case


def refusal(case: Case, rules: AcceptanceRules) -> Declined | None:
    request = QuoteRequest(
        sorted(rules.planos_validos)[0], case.idade, int(case.veiculo_texto.rsplit(" ", 1)[1])
    )
    return rules.evaluate(request, date.fromisoformat(case.reference_date))


def negative_cases(cases: Iterable[Case], rules: AcceptanceRules) -> tuple[Case, ...]:
    return tuple(case for case in cases if refusal(case, rules) is not None)


def stratified_sample(
    cases: Iterable[Case],
    rules: AcceptanceRules,
    per_stratum: int = 2,
) -> tuple[Case, ...]:
    groups: dict[tuple[str, bool, bool, str], list[Case]] = defaultdict(list)
    for case in sorted(cases, key=lambda case: case.conversation_id):
        result = refusal(case, rules)
        groups[
            (
                result.motivo if result else "aceito",
                any(
                    text.startswith(("[audio]", "[imagem]", "[documento]"))
                    for text in case.messages
                ),
                "[CEP]" in " ".join(case.messages),
                case.outcome,
            )
        ].append(case)
    return tuple(case for key in sorted(groups) for case in groups[key][:per_stratum])
