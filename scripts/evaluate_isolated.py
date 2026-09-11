"""Evaluate extraction alone with model-specific record/replay captures."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from tests.golden.isolated import run_isolated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("record", "replay"),
        default=os.environ.get("LLM_EVAL_MODE", "replay"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LLM_EXTRACTOR_MODEL", "openai/gpt-4.1-mini"),
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("tests/fixtures/llm-isolated"),
    )
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--retry-collection-failures",
        action="store_true",
        help="Arquiva capturas de erro deste modelo antes de retomar a gravação",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(
            run_isolated(
                mode=args.mode,
                directory=args.directory,
                model=args.model,
                dataset=args.dataset,
                concurrency=args.concurrency,
                limit=args.limit,
                technical_timeout=args.timeout,
                retry_collection_failures=args.retry_collection_failures,
            )
        )
    except (ValueError, FileNotFoundError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
