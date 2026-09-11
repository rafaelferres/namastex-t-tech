"""Run real extraction captures explicitly; replay is offline and the default.

Run from the repository root: uv run python -m scripts.evaluate_extraction.
Replay requires cases.json and manifest.json created by a prior real recording.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

import httpx

from application.llm import LLMRole
from application.ports import SystemClock
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.llm.recording import RecordedLLMClient
from interfaces.replay import dataset_path, read_rows
from tests.golden.evaluation import (
    audit_private_cep,
    cases_from_rows,
    evaluate_cases,
    load_cases,
    save_cases,
)


async def run(
    *,
    mode: str,
    directory: Path,
    dataset: Path | None = None,
    limit: int | None = None,
    concurrency: int = 4,
) -> dict[str, object]:
    if mode not in ("record", "replay"):
        raise ValueError("LLM_EVAL_MODE deve ser record ou replay")
    if mode == "record":
        if not os.environ.get("OPENROUTER_API_KEY", "").strip():
            raise ValueError("Gravação real exige OPENROUTER_API_KEY; nenhuma chamada foi feita")
        config = LLMConfig.from_env()
        source = dataset or dataset_path()
        if not source.is_file():
            raise FileNotFoundError("Corpus ausente; configure AUTOSEGURO_DATASET ou --dataset")
        rows = read_rows(source)
        cases = cases_from_rows(rows)
        if limit is not None:
            cases = cases[:limit]
        selected = {case.conversation_id for case in cases}
        audit = audit_private_cep(row for row in rows if str(row["conversation_id"]) in selected)
        save_cases(directory / "cases.json", cases)
        # Configuration is persisted without credentials, to reconstruct identical requests.
        configuration = {key: value for key, value in asdict(config).items() if key != "api_key"}
        manifest = {
            "version": 1,
            "source": "real_recording",
            "config": configuration,
            "private_cep_audit": audit,
            "case_count": len(cases),
        }
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        models = {role: config.model_for(role) for role in LLMRole}
        async with httpx.AsyncClient() as http:
            client = RecordedLLMClient(
                OpenRouterLLMClient(http, config, SystemClock()),
                directory / "responses",
                "record",
                models,
                settings=configuration,
            )
            report = await evaluate_cases(
                cases,
                client,
                budget=config.budget_seconds,
                token_limit=config.conversation_token_limit,
                concurrency=concurrency,
            )
        report["private_cep_audit"] = audit
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    cases = load_cases(directory / "cases.json")
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Manifest de gravação real ausente; replay não chama a rede")
    manifest = json.loads(manifest_path.read_text())
    config = LLMConfig(api_key="offline-replay", **manifest["config"])
    models = {role: config.model_for(role) for role in LLMRole}
    client = RecordedLLMClient(
        None, directory / "responses", "replay", models, settings=manifest["config"]
    )
    report = await evaluate_cases(
        cases,
        client,
        budget=config.budget_seconds,
        token_limit=config.conversation_token_limit,
        concurrency=concurrency,
    )
    report["private_cep_audit"] = manifest["private_cep_audit"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("record", "replay"), default=os.environ.get("LLM_EVAL_MODE", "replay")
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(os.environ.get("LLM_EVAL_FIXTURES", "tests/fixtures/llm-evaluation")),
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--limit", type=int, help="Gravação parcial explicitamente identificada")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit deve ser positivo")
    try:
        report = asyncio.run(
            run(
                mode=args.mode,
                directory=args.fixtures,
                dataset=args.dataset,
                limit=args.limit,
                concurrency=args.concurrency,
            )
        )
    except (ValueError, FileNotFoundError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
