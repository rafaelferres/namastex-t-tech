from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from application.llm import LLMRequest, LLMResponse, LLMUnavailable
from tests.golden.evaluation import EvaluationCase


def response(
    content: dict[str, object],
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 2,
    cost: Decimal | None = Decimal("0.01"),
    latency_ms: float = 10,
) -> LLMResponse:
    return LLMResponse(
        json.dumps(content),
        "provider/model-a",
        prompt_tokens,
        completion_tokens,
        cost,
        latency_ms,
    )


def informed(value: object) -> dict[str, object]:
    return {"valor": value, "status": "informado", "proveniencia": "digitado"}


@pytest.mark.asyncio
async def test_isolated_harness_accumulates_bursts_without_conversation_budget() -> None:
    from tests.golden.isolated import evaluate_isolated

    class Client:
        def __init__(self) -> None:
            self.requests: list[LLMRequest] = []

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                return response(
                    {"veiculo_ano": informed(2020)},
                    prompt_tokens=5000,
                    completion_tokens=1000,
                )
            return response(
                {"idade": informed(35)},
                prompt_tokens=5000,
                completion_tokens=1000,
            )

    client = Client()
    report = await evaluate_isolated(
        (EvaluationCase("conv-a", ("Argo 2020", "tenho 35 anos"), 35, 2020, "outro"),),
        client,
        technical_timeout=30,
    )

    assert len(client.requests) == 2
    assert [request.budget for request in client.requests] == [30, 30]
    assert "2020" in client.requests[1].user
    assert "Argo 2020" not in client.requests[1].user
    assert report["collection"] == {
        "successful_cases": 1,
        "failed_cases": 0,
        "coverage": 1.0,
        "failures_by_kind": {},
        "failed_case_ids": [],
    }
    assert report["slots"]["idade"]["accuracy_on_collected_cases"] == 1.0
    assert report["slots"]["idade"]["accuracy_all_cases"] == 1.0
    assert report["slots"]["veiculo_ano"]["accuracy_on_collected_cases"] == 1.0
    assert report["slots"]["veiculo_ano"]["accuracy_all_cases"] == 1.0
    assert report["usage"]["prompt_tokens"] == 10_000


@pytest.mark.asyncio
async def test_isolated_report_separates_collection_failures_from_semantic_errors() -> None:
    from tests.golden.isolated import evaluate_isolated

    class Client:
        def __init__(self) -> None:
            self.calls: dict[str, int] = {}

        async def complete(self, request: LLMRequest) -> LLMResponse:
            if request.conversation_id == "failed":
                position = self.calls.get(request.conversation_id, 0)
                self.calls[request.conversation_id] = position + 1
                if position == 0:
                    return response({"idade": informed(35)}, latency_ms=30)
                raise LLMUnavailable(latency_ms=40)
            if request.conversation_id == "correct":
                return response(
                    {"idade": informed(35), "veiculo_ano": informed(2020)},
                    latency_ms=10,
                )
            if request.conversation_id == "wrong":
                return response(
                    {"idade": informed(36), "veiculo_ano": informed(2021)},
                    cost=Decimal("0.02"),
                    latency_ms=20,
                )
            return response({}, cost=None, latency_ms=30)

    cases = (
        EvaluationCase("correct", ("a",), 35, 2020, "outro"),
        EvaluationCase("wrong", ("b",), 35, 2020, "outro"),
        EvaluationCase("missing", ("c",), 35, 2020, "outro"),
        EvaluationCase("failed", ("d", "e"), 35, 2020, "outro"),
    )
    report = await evaluate_isolated(cases, Client(), concurrency=2)

    assert report["total_cases"] == 4
    assert report["collection"] == {
        "successful_cases": 3,
        "failed_cases": 1,
        "coverage": 0.75,
        "failures_by_kind": {"unavailable": 1},
        "failed_case_ids": ["failed"],
    }
    assert report["slots"]["idade"] == {
        "collected_cases": 3,
        "predicted_on_collected_cases": 2,
        "correct_on_collected_cases": 1,
        "semantic_errors_on_collected_cases": 2,
        "accuracy_on_collected_cases": 1 / 3,
        "correct_all_cases": 2,
        "accuracy_all_cases": 0.5,
        "prediction_coverage_on_collected_cases": 2 / 3,
    }
    assert report["slots"]["veiculo_ano"] == {
        "collected_cases": 3,
        "predicted_on_collected_cases": 2,
        "correct_on_collected_cases": 1,
        "semantic_errors_on_collected_cases": 2,
        "accuracy_on_collected_cases": 1 / 3,
        "correct_all_cases": 1,
        "accuracy_all_cases": 0.25,
        "prediction_coverage_on_collected_cases": 2 / 3,
    }
    assert report["usage"] == {
        "calls": 5,
        "responses_with_usage": 4,
        "calls_with_latency": 5,
        "prompt_tokens": 40,
        "completion_tokens": 8,
        "known_cost": "0.04",
        "cost_complete": False,
        "latency_median_ms": 30,
        "latency_p95_ms": 40,
        "observed_models": ["provider/model-a"],
    }


@pytest.mark.asyncio
async def test_isolated_replay_missing_fixture_is_fatal(tmp_path: Path) -> None:
    from application.llm import LLMRole
    from infrastructure.llm.recording import LLMFixtureMissing, RecordedLLMClient
    from tests.golden.isolated import evaluate_isolated

    client = RecordedLLMClient(
        None,
        tmp_path,
        "replay",
        {LLMRole.EXTRACTOR: "provider/model-a"},
    )

    with pytest.raises(LLMFixtureMissing):
        await evaluate_isolated(
            (EvaluationCase("conv-a", ("oi",), 35, 2020, "outro"),),
            client,
        )


def test_model_directory_is_distinct_and_cannot_escape_root(tmp_path: Path) -> None:
    from tests.golden.isolated import model_directory

    first = model_directory(tmp_path, "openai/gpt-4.1-mini")
    second = model_directory(tmp_path, "openai/gpt-4.1-nano")
    escaped = model_directory(tmp_path, "../../outside")

    assert first != second
    assert first.parent == second.parent == escaped.parent == tmp_path / "models"
    assert first.name.startswith("openai-gpt-4.1-mini-")
    assert escaped.name.startswith("outside-")


def test_retry_collection_failures_archives_evidence_outside_responses(tmp_path: Path) -> None:
    from tests.golden.isolated import archive_collection_failure_fixtures

    capture = tmp_path / "model"
    responses = capture / "responses"
    responses.mkdir(parents=True)
    success = responses / "success.json"
    unavailable = responses / "unavailable.json"
    contract = responses / "contract.json"
    invalid = responses / "invalid.json"
    success.write_text(json.dumps({"response": {"content": "{}"}}))
    unavailable.write_text(json.dumps({"error": "unavailable"}))
    contract.write_text(json.dumps({"error": "contract_error"}))
    invalid.write_text("not json")
    previous_report = capture / "report.json"
    previous_report.write_text(json.dumps({"collection": {"failed_cases": 2}}))

    archive = archive_collection_failure_fixtures(capture)

    assert archive == {
        "archived_failure_fixtures": 2,
        "archived_report": True,
        "history_run": "retry-0001",
    }
    assert success.is_file()
    assert invalid.is_file()
    assert not unavailable.exists()
    assert not contract.exists()
    assert not previous_report.exists()
    history = capture / "history" / "retry-0001"
    assert (history / "responses" / unavailable.name).is_file()
    assert (history / "responses" / contract.name).is_file()
    assert (history / "report.json").is_file()
    assert json.loads((history / "archive.json").read_text())["usage_excluded_from_next_report"]


@pytest.mark.asyncio
async def test_model_specific_capture_replays_offline_without_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from application.llm import LLMRole
    from infrastructure.llm.recording import RecordedLLMClient
    from tests.golden.evaluation import save_cases
    from tests.golden.isolated import evaluate_isolated, model_directory, run_isolated

    class Client:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            return response({"idade": informed(35), "veiculo_ano": informed(2020)})

    model = "provider/model-a"
    cases = (EvaluationCase("conv-a", ("mensagem redigida",), 35, 2020, "outro"),)
    save_cases(tmp_path / "cases.json", cases)
    capture_dir = model_directory(tmp_path, model)
    settings = {"harness": "isolated-extraction", "technical_timeout_seconds": 30.0}
    recorder = RecordedLLMClient(
        Client(),
        capture_dir / "responses",
        "record",
        {LLMRole.EXTRACTOR: model},
        settings=settings,
    )
    await evaluate_isolated(cases, recorder)
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "harness": "isolated-extraction",
                "model": model,
                "technical_timeout_seconds": 30.0,
                "corpus_case_count": 1,
                "private_cep_audit": {},
            }
        )
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = await run_isolated(mode="replay", directory=tmp_path, model=model)

    assert report["total_cases"] == 1
    assert report["requested_model"] == model
    assert report["mode"] == "replay"
    assert report["collection"]["coverage"] == 1.0
