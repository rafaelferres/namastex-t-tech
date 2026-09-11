"""Isolated slot-extraction evaluation without service or conversation budgets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from statistics import median

import httpx

from agent.nodes.extract import (
    ExtractionContractError,
    ExtractionUnavailable,
    SlotExtractor,
)
from agent.schemas.slots import Slots
from application.llm import (
    LLMClient,
    LLMContractError,
    LLMRequest,
    LLMResponse,
    LLMRole,
    LLMUnavailable,
)
from application.ports import SystemClock
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.llm.recording import LLMFixtureInvalid, LLMFixtureMissing, RecordedLLMClient
from infrastructure.privacy import PrivacyRedactor
from interfaces.replay import dataset_path, read_rows
from tests.golden.evaluation import (
    EvaluationCase,
    audit_private_cep,
    cases_from_rows,
    load_cases,
    save_cases,
)


@dataclass(frozen=True, slots=True)
class _CaseResult:
    conversation_id: str
    collection_failure: str | None
    idade_predicted: bool
    idade_correct: bool
    ano_predicted: bool
    ano_correct: bool


class _ObservedClient:
    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.calls = 0
        self.responses: list[LLMResponse] = []
        self.latencies: list[float] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        try:
            response = await self._inner.complete(request)
        except (LLMFixtureMissing, LLMFixtureInvalid):
            raise
        except (LLMUnavailable, LLMContractError) as error:
            if error.latency_ms is not None:
                self.latencies.append(error.latency_ms)
            raise
        self.responses.append(response)
        self.latencies.append(response.latency_ms)
        return response


def model_directory(root: Path, model: str) -> Path:
    """Return a stable, traversal-safe capture directory for one model."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("._-") or "model"
    digest = hashlib.sha256(model.encode()).hexdigest()[:10]
    return root / "models" / f"{normalized[:80]}-{digest}"


def archive_collection_failure_fixtures(capture_dir: Path) -> dict[str, object]:
    """Move failed attempts and the prior report out of the active response set."""
    responses = capture_dir / "responses"
    failures: list[Path] = []
    if not responses.is_dir():
        return {
            "archived_failure_fixtures": 0,
            "archived_report": False,
            "history_run": None,
        }
    for path in responses.glob("*.json"):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("error") in {
            "unavailable",
            "contract_error",
        }:
            failures.append(path)
    if not failures:
        return {
            "archived_failure_fixtures": 0,
            "archived_report": False,
            "history_run": None,
        }

    history_root = capture_dir / "history"
    index = 1
    while (history_root / f"retry-{index:04d}").exists():
        index += 1
    history_run = f"retry-{index:04d}"
    destination = history_root / history_run
    archived_responses = destination / "responses"
    archived_responses.mkdir(parents=True)
    for path in failures:
        path.replace(archived_responses / path.name)
    previous_report = capture_dir / "report.json"
    archived_report = previous_report.is_file()
    if archived_report:
        previous_report.replace(destination / "report.json")
    archive = {
        "archived_failure_fixtures": len(failures),
        "archived_report": archived_report,
        "history_run": history_run,
    }
    (destination / "archive.json").write_text(
        json.dumps(
            {
                **archive,
                "usage_excluded_from_next_report": True,
                "scope": "Tentativas de falha anteriores não compõem o novo relatório",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return archive


async def run_isolated(
    *,
    mode: str,
    directory: Path,
    model: str,
    dataset: Path | None = None,
    concurrency: int = 4,
    limit: int | None = None,
    technical_timeout: float = 30.0,
    retry_collection_failures: bool = False,
) -> dict[str, object]:
    """Record or replay one model while keeping the shared corpus unchanged."""
    _validate_run_options(
        mode=mode,
        model=model,
        concurrency=concurrency,
        limit=limit,
        technical_timeout=technical_timeout,
        retry_collection_failures=retry_collection_failures,
    )
    capture_dir = model_directory(directory, model)
    manifest_path = capture_dir / "manifest.json"
    cases_path = directory / "cases.json"
    settings = _capture_settings(technical_timeout)

    if mode == "record":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key.strip():
            raise ValueError("Gravação real exige OPENROUTER_API_KEY; nenhuma chamada foi feita")
        source = dataset or dataset_path()
        if not source.is_file():
            raise FileNotFoundError("Corpus ausente; configure AUTOSEGURO_DATASET ou --dataset")
        rows = read_rows(source)
        corpus = cases_from_rows(rows)
        if cases_path.is_file() and load_cases(cases_path) != corpus:
            raise ValueError("Corpus difere dos casos já compartilhados entre modelos")
        if not cases_path.is_file():
            save_cases(cases_path, corpus)
        selected = corpus[:limit] if limit is not None else corpus
        audit = audit_private_cep(rows)
        manifest = {
            "version": 1,
            "harness": "isolated-extraction",
            "model": model,
            "technical_timeout_seconds": technical_timeout,
            "corpus_case_count": len(corpus),
            "private_cep_audit": audit,
        }
        _ensure_compatible_manifest(manifest_path, manifest)
        capture_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        retry_archive = (
            archive_collection_failure_fixtures(capture_dir)
            if retry_collection_failures
            else {
                "archived_failure_fixtures": 0,
                "archived_report": False,
                "history_run": None,
            }
        )
        config = LLMConfig(
            api_key=api_key,
            extractor_model=model,
            conversation_model=f"{model}#unused-conversation-role",
            timeout_seconds=technical_timeout,
            budget_seconds=technical_timeout,
            conversation_token_limit=1,
        )
        async with httpx.AsyncClient() as http:
            recorded = RecordedLLMClient(
                OpenRouterLLMClient(http, config, SystemClock()),
                capture_dir / "responses",
                "record",
                {LLMRole.EXTRACTOR: model},
                settings=settings,
            )
            report = await evaluate_isolated(
                selected,
                recorded,
                technical_timeout=technical_timeout,
                concurrency=concurrency,
            )
        report["private_cep_audit"] = audit
        report["retry_archive"] = retry_archive
        report["usage_scope"] = "O novo relatório exclui tentativas de falha arquivadas"
        report["archived_failure_attempts_excluded"] = retry_archive[
            "archived_failure_fixtures"
        ]
    else:
        manifest = _load_manifest(manifest_path)
        _validate_replay_manifest(manifest, model, technical_timeout)
        corpus = load_cases(cases_path)
        if manifest["corpus_case_count"] != len(corpus):
            raise ValueError("Manifesto não corresponde ao corpus compartilhado")
        selected = corpus[:limit] if limit is not None else corpus
        recorded = RecordedLLMClient(
            None,
            capture_dir / "responses",
            "replay",
            {LLMRole.EXTRACTOR: model},
            settings=settings,
        )
        report = await evaluate_isolated(
            selected,
            recorded,
            technical_timeout=technical_timeout,
            concurrency=concurrency,
        )
        report["private_cep_audit"] = manifest["private_cep_audit"]

    report.update(
        {
            "mode": mode,
            "requested_model": model,
            "technical_timeout_seconds": technical_timeout,
            "corpus_cases": len(corpus),
            "limited": limit is not None,
        }
    )
    if mode == "record":
        (capture_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
    return report


def _capture_settings(technical_timeout: float) -> dict[str, object]:
    return {
        "harness": "isolated-extraction",
        "technical_timeout_seconds": technical_timeout,
    }


def _validate_run_options(
    *,
    mode: str,
    model: str,
    concurrency: int,
    limit: int | None,
    technical_timeout: float,
    retry_collection_failures: bool,
) -> None:
    if mode not in {"record", "replay"}:
        raise ValueError("Modo deve ser record ou replay")
    if not model.strip():
        raise ValueError("Modelo deve ser informado")
    if concurrency < 1:
        raise ValueError("Concorrência deve ser positiva")
    if limit is not None and limit <= 0:
        raise ValueError("Limite deve ser positivo")
    if not math.isfinite(technical_timeout) or technical_timeout <= 0:
        raise ValueError("Timeout técnico deve ser positivo e finito")
    if retry_collection_failures and mode != "record":
        raise ValueError("Retomada de falhas só está disponível em modo record")


def _ensure_compatible_manifest(path: Path, expected: dict[str, object]) -> None:
    if not path.is_file():
        return
    current = _load_manifest(path)
    for key in ("version", "harness", "model", "technical_timeout_seconds"):
        if current.get(key) != expected[key]:
            raise ValueError("Capturas existentes usam configuração incompatível")


def _load_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError("Manifesto do modelo ausente; replay nunca chama a rede")
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        raise ValueError("Manifesto do modelo inválido") from None
    if not isinstance(manifest, dict):
        raise ValueError("Manifesto do modelo inválido")
    return manifest


def _validate_replay_manifest(
    manifest: dict[str, object], model: str, technical_timeout: float
) -> None:
    expected = {
        "version": 1,
        "harness": "isolated-extraction",
        "model": model,
        "technical_timeout_seconds": technical_timeout,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError("Manifesto não corresponde ao modelo e timeout solicitados")
    if type(manifest.get("corpus_case_count")) is not int:
        raise ValueError("Manifesto do modelo inválido")
    audit = manifest.get("private_cep_audit")
    if not isinstance(audit, dict):
        raise ValueError("Manifesto do modelo inválido")


async def evaluate_isolated(
    cases: Iterable[EvaluationCase],
    client: LLMClient,
    privacy: PrivacyRedactor | None = None,
    *,
    technical_timeout: float = 30.0,
    concurrency: int = 4,
) -> dict[str, object]:
    """Feed every lead burst to ``SlotExtractor`` and report collection separately."""
    selected = tuple(cases)
    if not selected:
        raise ValueError("Conjunto de avaliação vazio")
    if concurrency < 1:
        raise ValueError("Concorrência deve ser positiva")
    if not math.isfinite(technical_timeout) or technical_timeout <= 0:
        raise ValueError("Timeout técnico deve ser positivo e finito")

    observed = _ObservedClient(client)
    extractor = SlotExtractor(
        observed,
        privacy or PrivacyRedactor(),
        default_budget=technical_timeout,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(case: EvaluationCase) -> _CaseResult:
        state = Slots()
        failure: str | None = None
        async with semaphore:
            for turn in case.turns:
                try:
                    result = await extractor.extract(
                        turn,
                        state,
                        conversation_id=case.conversation_id,
                    )
                except (LLMFixtureMissing, LLMFixtureInvalid):
                    raise
                except ExtractionUnavailable as error:
                    state = error.slots
                    failure = "unavailable"
                    break
                except ExtractionContractError as error:
                    state = error.slots
                    failure = "contract_error"
                    break
                state = result.slots
                if result.tokens_esgotados:
                    failure = "token_budget_exceeded"
                    break
                if _is_informed(state.idade) and _is_informed(state.veiculo_ano):
                    break
        idade = state.idade
        ano = state.veiculo_ano
        idade_predicted = _is_informed(idade)
        ano_predicted = _is_informed(ano)
        return _CaseResult(
            conversation_id=case.conversation_id,
            collection_failure=failure,
            idade_predicted=idade_predicted,
            idade_correct=idade_predicted and idade is not None and idade.valor == case.idade,
            ano_predicted=ano_predicted,
            ano_correct=ano_predicted and ano is not None and ano.valor == case.veiculo_ano,
        )

    tasks = [asyncio.create_task(evaluate_one(case)) for case in selected]
    try:
        results = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    successful = [item for item in results if item.collection_failure is None]
    failed = [item for item in results if item.collection_failure is not None]
    failures = Counter(item.collection_failure for item in failed)
    return {
        "total_cases": len(selected),
        "collection": {
            "successful_cases": len(successful),
            "failed_cases": len(failed),
            "coverage": len(successful) / len(selected),
            "failures_by_kind": dict(sorted(failures.items())),
            "failed_case_ids": sorted(item.conversation_id for item in failed),
        },
        "slots": {
            "idade": _slot_report(
                results,
                successful,
                predicted="idade_predicted",
                correct="idade_correct",
            ),
            "veiculo_ano": _slot_report(
                results,
                successful,
                predicted="ano_predicted",
                correct="ano_correct",
            ),
        },
        "usage": _usage_report(observed),
    }


def _is_informed(value: object) -> bool:
    return value is not None and getattr(value, "status", None) == "informado"


def _slot_report(
    all_results: list[_CaseResult],
    collected_results: list[_CaseResult],
    *,
    predicted: str,
    correct: str,
) -> dict[str, int | float | None]:
    collected = len(collected_results)
    predicted_count = sum(bool(getattr(item, predicted)) for item in collected_results)
    correct_count = sum(bool(getattr(item, correct)) for item in collected_results)
    correct_all = sum(bool(getattr(item, correct)) for item in all_results)
    return {
        "collected_cases": collected,
        "predicted_on_collected_cases": predicted_count,
        "correct_on_collected_cases": correct_count,
        "semantic_errors_on_collected_cases": collected - correct_count,
        "accuracy_on_collected_cases": correct_count / collected if collected else None,
        "correct_all_cases": correct_all,
        "accuracy_all_cases": correct_all / len(all_results),
        "prediction_coverage_on_collected_cases": (
            predicted_count / collected if collected else None
        ),
    }


def _usage_report(observed: _ObservedClient) -> dict[str, object]:
    latencies = sorted(observed.latencies)
    costs = [response.cost for response in observed.responses]
    return {
        "calls": observed.calls,
        "responses_with_usage": len(observed.responses),
        "calls_with_latency": len(latencies),
        "prompt_tokens": sum(response.prompt_tokens for response in observed.responses),
        "completion_tokens": sum(response.completion_tokens for response in observed.responses),
        "known_cost": str(sum((cost for cost in costs if cost is not None), Decimal(0))),
        "cost_complete": (
            observed.calls == len(observed.responses)
            and bool(costs)
            and all(cost is not None for cost in costs)
        ),
        "latency_median_ms": median(latencies) if latencies else None,
        "latency_p95_ms": (
            latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None
        ),
        "observed_models": sorted({response.model for response in observed.responses}),
    }
