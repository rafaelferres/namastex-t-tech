from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from application.llm import LLMRequest, LLMResponse
from tests.golden.evaluation import (
    EvaluationCase,
    cases_from_rows,
    load_cases,
    save_cases,
    summarize,
)


def test_loader_orders_bursts_and_excludes_seller_and_pii() -> None:
    def row(index: int, role: str, body: str) -> dict[str, object]:
        return dict(
            conversation_id="conv_1",
            message_index=index,
            sender_role=role,
            message_body=body,
            lead_idade_informada=35,
            veiculo_texto="Fiat Argo 2020",
        )

    cases = cases_from_rows(
        [
            row(4, "lead", "CEP 07000-000"),
            row(2, "vendedor", "R$ 100"),
            row(3, "lead", "tenho 35 anos"),
            row(1, "lead", "Argo, ano 2020"),
        ]
    )
    assert cases == (
        EvaluationCase(
            "conv_1", ("Argo, ano 2020", "tenho 35 anos\nCEP [CEP]"), 35, 2020, "modelo_ano"
        ),
    )


def test_cases_roundtrip_is_independent_of_original_corpus(tmp_path: Path) -> None:
    cases = (EvaluationCase("conv_1", ("tenho 35 anos",), 35, 2020, "outro"),)
    save_cases(tmp_path / "cases.json", cases)
    assert load_cases(tmp_path / "cases.json") == cases
    assert "35" in json.dumps(json.loads((tmp_path / "cases.json").read_text()))
    with pytest.raises(FileNotFoundError, match="gravação real"):
        load_cases(tmp_path / "missing.json")


def test_summary_reports_unknown_cost_and_nearest_rank_percentile() -> None:
    report = summarize(
        total=2,
        idade_correct=1,
        ano_correct=2,
        failures={"modelo_ano": 1},
        latencies=[10.0, 20.0, 30.0],
        prompt_tokens=10,
        completion_tokens=4,
        costs=[Decimal("0.01"), None, Decimal("0.02")],
        cep_integer_responses=1,
        exhausted=1,
    )
    assert report["idade_accuracy"] == 0.5
    assert report["veiculo_ano_accuracy"] == 1
    assert report["latency_median_ms"] == 20
    assert report["latency_p95_ms"] == 30
    assert report["known_cost"] == "0.03"
    assert report["cost_complete"] is False
    assert report["cep_integer_responses"] == 1
    assert report["cep_accuracy"] is None
    assert report["budget_exhausted_cases"] == 1


@pytest.mark.asyncio
async def test_runner_uses_current_turn_preserves_state_and_stops_at_qualification() -> None:
    from application.llm import LLMResponse
    from tests.golden.evaluation import evaluate_cases

    class Client:
        def __init__(self) -> None:
            self.requests: list[LLMRequest] = []

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            slot = "veiculo_ano" if len(self.requests) == 1 else "idade"
            value = 2020 if len(self.requests) == 1 else 35
            return LLMResponse(
                json.dumps({slot: dict(valor=value, status="informado", proveniencia="digitado")}),
                "test-model",
                10,
                2,
                Decimal("0.01"),
                15.0,
            )

    client = Client()
    report = await evaluate_cases(
        (
            EvaluationCase(
                "corpus-unique",
                ("Argo 2020", "tenho 35 anos", "não enviar"),
                99,
                1999,
                "marca_modelo_ano",
            ),
        ),
        client,
    )
    assert len(client.requests) == 2
    assert all(request.conversation_id == "corpus-unique" for request in client.requests)
    assert "Argo 2020" not in client.requests[1].user
    assert "2020" in client.requests[1].user
    assert "1999" not in client.requests[1].user
    assert report["idade_accuracy"] == 0
    assert report["veiculo_ano_accuracy"] == 0
    assert report["prompt_tokens"] == 20


def test_private_cep_audit_counts_all_cases_without_persisting_pii() -> None:
    from tests.golden.evaluation import audit_private_cep

    rows = [
        dict(
            conversation_id="c1", message_index=1, sender_role="lead", message_body="CEP 07000-000"
        ),
        dict(conversation_id="c2", message_index=1, sender_role="lead", message_body="oi"),
    ]
    report = audit_private_cep(rows)
    assert report["total"] == 2
    assert report["correct"] == 1
    assert report["missing"] == 1
    assert report["integer"] == 0
    assert "07000" not in json.dumps(report)


@pytest.mark.asyncio
async def test_missing_fixture_is_fatal_instead_of_accuracy_failure(tmp_path: Path) -> None:
    from application.llm import LLMRole
    from infrastructure.llm.recording import LLMFixtureMissing, RecordedLLMClient
    from tests.golden.evaluation import evaluate_cases

    client = RecordedLLMClient(None, tmp_path, "replay", {LLMRole.EXTRACTOR: "model"})
    with pytest.raises(LLMFixtureMissing):
        await evaluate_cases((EvaluationCase("id", ("oi",), 35, 2020, "outro"),), client)


@pytest.mark.asyncio
async def test_invalid_integer_cep_is_counted_even_when_schema_rejects_response() -> None:
    from application.llm import LLMResponse
    from tests.golden.evaluation import evaluate_cases

    class Client:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                json.dumps(
                    {"cep": dict(valor=7000000, status="informado", proveniencia="digitado")}
                ),
                "test",
                1,
                2,
                None,
                10,
            )

    report = await evaluate_cases((EvaluationCase("id", ("oi",), 35, 2020, "outro"),), Client())
    assert report["cep_integer_responses"] == 1
    assert report["total"] == 1
    assert report["idade_accuracy"] == 0
    assert report["prompt_tokens"] == 1


@pytest.mark.asyncio
async def test_failed_call_is_counted_but_unknown_cost_not_invented() -> None:
    from application.llm import LLMUnavailable
    from tests.golden.evaluation import evaluate_cases

    class Client:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            raise LLMUnavailable()

    report = await evaluate_cases((EvaluationCase("id", ("oi",), 35, 2020, "outro"),), Client())
    assert report["calls"] == 1
    assert report["responses_with_usage"] == 0
    assert report["cost_complete"] is False
    assert report["provider_error_cases"] == 1


@pytest.mark.asyncio
async def test_record_without_credential_has_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.golden.runner import run

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        await run(mode="record", directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_budget_keeps_usage_of_response_which_exhausts_limit() -> None:
    from application.llm import LLMResponse
    from tests.golden.evaluation import evaluate_cases

    class Client:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse("{}", "test", 100, 50, Decimal(".01"), 20)

    report = await evaluate_cases(
        (EvaluationCase("id", ("oi", "olá"), 35, 2020, "outro"),), Client(), token_limit=10
    )
    assert report["calls"] == 1
    assert report["prompt_tokens"] == 100
    assert report["completion_tokens"] == 50
    assert report["known_cost"] == "0.01"
    assert report["budget_exhausted_cases"] == 1


@pytest.mark.asyncio
async def test_concurrency_is_bounded_and_case_identity_is_preserved() -> None:
    import asyncio

    from application.llm import LLMResponse
    from tests.golden.evaluation import evaluate_cases

    class Client:
        active = 0
        peak = 0
        ids: set[str] = set()

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.ids.add(request.conversation_id)
            await asyncio.sleep(0)
            self.active -= 1
            return LLMResponse("{}", "test", 1, 1, None, 1)

    client = Client()
    cases = tuple(EvaluationCase(str(i), ("oi",), 35, 2020, "outro") for i in range(5))
    report = await evaluate_cases(cases, client, concurrency=2)
    assert client.peak == 2
    assert client.ids == {str(i) for i in range(5)}
    assert report["total"] == 5
    assert report["calls"] == 5
