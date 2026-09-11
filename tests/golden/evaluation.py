"""LLM corpus evaluation: redacted turns, independent labels and measured usage."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from statistics import median

from application.llm import LLMClient, LLMRequest, LLMResponse
from infrastructure.privacy import PrivacyRedactor


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    conversation_id: str
    turns: tuple[str, ...]
    idade: int
    veiculo_ano: int
    formato: str


def cases_from_rows(rows: Iterable[Mapping[str, object]]) -> tuple[EvaluationCase, ...]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["conversation_id"])].append(row)
    privacy = PrivacyRedactor()
    cases = []
    for conversation_id, group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda row: int(str(row["message_index"])))
        turns: list[str] = []
        burst: list[str] = []
        for row in ordered:
            if row["sender_role"] == "lead":
                burst.append(privacy.redact(str(row["message_body"])))
            elif burst:
                turns.append("\n".join(burst))
                burst = []
        if burst:
            turns.append("\n".join(burst))
        # Ground truth is read only here; it is never used to select input turns.
        label = str(ordered[0]["veiculo_texto"])
        year = int(label.rsplit(" ", 1)[1])
        vehicle_turn = next((turn for turn in turns if re.search(r"\b(?:19|20)\d{2}\b", turn)), "")
        if ", ano " in vehicle_turn:
            formato = "modelo_ano"
        elif vehicle_turn.startswith("e um "):
            formato = "e_um_modelo"
        elif vehicle_turn:
            formato = "marca_modelo_ano"
        else:
            formato = "outro"
        cases.append(
            EvaluationCase(
                conversation_id,
                tuple(turns),
                int(str(ordered[0]["lead_idade_informada"])),
                year,
                formato,
            )
        )
    return tuple(cases)


def save_cases(path: Path, cases: Iterable[EvaluationCase]) -> None:
    privacy = PrivacyRedactor()
    rows = [asdict(case) for case in cases]
    for row in rows:
        row["turns"] = [privacy.redact(turn) for turn in row["turns"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


def load_cases(path: Path) -> tuple[EvaluationCase, ...]:
    if not path.is_file():
        raise FileNotFoundError(
            "Casos de replay ausentes; execute gravação real autorizada primeiro"
        )
    rows = json.loads(path.read_text())
    cases = tuple(EvaluationCase(**{**row, "turns": tuple(row["turns"])}) for row in rows)
    if not cases:
        raise ValueError("Conjunto de avaliação vazio")
    return cases


def summarize(
    *,
    total: int,
    idade_correct: int,
    ano_correct: int,
    failures: Mapping[str, int],
    latencies: list[float],
    prompt_tokens: int,
    completion_tokens: int,
    costs: list[Decimal | None],
    cep_integer_responses: int,
    exhausted: int,
) -> dict[str, object]:
    if not total:
        raise ValueError("Conjunto de avaliação vazio")
    ordered = sorted(latencies)
    return {
        "total": total,
        "idade_correct": idade_correct,
        "veiculo_ano_correct": ano_correct,
        "idade_accuracy": idade_correct / total,
        "veiculo_ano_accuracy": ano_correct / total,
        "failures_by_format": dict(failures),
        "calls": len(latencies),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "known_cost": str(sum((cost for cost in costs if cost is not None), Decimal(0))),
        "cost_complete": bool(costs) and all(cost is not None for cost in costs),
        "latency_median_ms": median(ordered) if ordered else None,
        "latency_p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None,
        "cep_integer_responses": cep_integer_responses,
        "cep_accuracy": None,
        "cep_scope": "Corpus redigido: captura privada de CEP não avaliada pelo LLM",
        "budget_exhausted_cases": exhausted,
    }


async def evaluate_cases(
    cases: Iterable[EvaluationCase],
    client: LLMClient,
    privacy: PrivacyRedactor | None = None,
    *,
    budget: float = 2.5,
    token_limit: int = 4000,
    concurrency: int = 1,
) -> dict[str, object]:
    from agent.nodes.extract import SlotExtractor
    from agent.schemas.slots import Slots
    from application.llm import LLMContractError, LLMUnavailable
    from infrastructure.llm.budget import BudgetedLLMClient
    from infrastructure.llm.recording import LLMFixtureInvalid, LLMFixtureMissing

    observed = _ObservedClient(client)
    extractor = SlotExtractor(
        BudgetedLLMClient(observed, token_limit),
        privacy or PrivacyRedactor(),
        default_budget=budget,
    )
    total = age = year = exhausted = provider_errors = 0
    failures: dict[str, int] = defaultdict(int)
    if concurrency < 1:
        raise ValueError("Concorrência deve ser positiva")

    async def evaluate_one(case: EvaluationCase) -> None:
        nonlocal total, age, year, exhausted, provider_errors
        state = Slots()
        for turn in case.turns:
            try:
                result = await extractor.extract(turn, state, conversation_id=case.conversation_id)
            except (LLMFixtureMissing, LLMFixtureInvalid):
                raise
            except (LLMUnavailable, LLMContractError) as error:
                state = getattr(error, "slots", state)
                provider_errors += 1
                break
            state = result.slots
            if result.tokens_esgotados:
                exhausted += 1
                break
            if (
                state.idade is not None
                and state.idade.status == "informado"
                and state.veiculo_ano is not None
                and state.veiculo_ano.status == "informado"
            ):
                break
        age_ok = (
            state.idade is not None
            and state.idade.status == "informado"
            and (state.idade.valor == case.idade)
        )
        year_ok = (
            state.veiculo_ano is not None
            and state.veiculo_ano.status == "informado"
            and (state.veiculo_ano.valor == case.veiculo_ano)
        )
        total += 1
        age += age_ok
        year += year_ok
        if not (age_ok and year_ok):
            failures[case.formato] += 1

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(case: EvaluationCase) -> None:
        async with semaphore:
            await evaluate_one(case)

    tasks = [asyncio.create_task(bounded(case)) for case in cases]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    report = summarize(
        total=total,
        idade_correct=age,
        ano_correct=year,
        failures=failures,
        latencies=[item.latency_ms for item in observed.responses],
        prompt_tokens=sum(item.prompt_tokens for item in observed.responses),
        completion_tokens=sum(item.completion_tokens for item in observed.responses),
        costs=[item.cost for item in observed.responses],
        cep_integer_responses=observed.cep_integer_responses,
        exhausted=exhausted,
    )
    report["responses_with_usage"] = len(observed.responses)
    report["calls"] = observed.calls
    report["provider_error_cases"] = provider_errors
    if observed.calls != len(observed.responses):
        report["cost_complete"] = False
    return report


class _ObservedClient:
    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.responses: list[LLMResponse] = []
        self.cep_integer_responses = 0
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        response = await self.inner.complete(request)
        self.responses.append(response)
        try:
            payload = json.loads(response.content)
            cep = payload.get("cep") if isinstance(payload, dict) else None
            value = cep.get("valor") if isinstance(cep, dict) else cep
            self.cep_integer_responses += type(value) is int
        except (ValueError, TypeError):
            pass
        return response


def audit_private_cep(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Measure private capture before redaction; persist only aggregate counts."""
    from agent.nodes.extract import capture_private_cep

    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["conversation_id"])].append(row)
    correct = missing = integer = leading_zero = 0
    for group in grouped.values():
        expected = None
        captured: object = None
        for row in sorted(group, key=lambda row: int(str(row["message_index"]))):
            if row["sender_role"] != "lead":
                continue
            message = str(row["message_body"])
            if expected is None:
                match = re.search(r"\bCEP\s*[:=]?\s*([0-9]{5}-?[0-9]{3})\b", message, re.I)
                if match:
                    expected = match[1].replace("-", "")
            if captured is None:
                captured = capture_private_cep(message)
        missing += expected is None or captured is None
        integer += type(captured) is int
        if expected is not None and captured is not None:
            correct += type(captured) is str and captured == expected
            leading_zero += expected.startswith("0") and str(captured) != expected
    return {
        "total": len(grouped),
        "correct": correct,
        "missing": missing,
        "integer": integer,
        "leading_zero_lost": leading_zero,
        "scope": "Captura determinística privada sobre corpus original antes da redação",
    }
