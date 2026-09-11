"""Mede insert + commit síncrono do trace em WAL, sem background."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from application.tracing import QuoteAttempt
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.connection import connect


def measure(parent: Path, samples: int) -> dict[str, object]:
    with TemporaryDirectory(dir=parent) as folder:
        connection = connect(Path(folder) / "trace.sqlite")
        store = SQLiteAttempts(connection)
        event = QuoteAttempt(
            "trace-benchmark",
            "conv-benchmark",
            "a" * 64,
            1,
            "quoted",
            "api",
            200,
            15,
            False,
            False,
            None,
            datetime.now(UTC),
        )
        try:
            durations = []
            for index in range(samples + 20):
                start = perf_counter_ns()
                store._write(event)  # The exact synchronous insert + commit used by the adapter.
                elapsed = (perf_counter_ns() - start) / 1_000_000
                if index >= 20:
                    durations.append(elapsed)
            ordered = sorted(durations)
            return {
                "parent": str(parent.resolve()),
                "samples": samples,
                "warmup": 20,
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
                "synchronous": connection.execute("PRAGMA synchronous").fetchone()[0],
                "median_ms": statistics.median(ordered),
                "p95_ms": ordered[math.ceil(samples * 0.95) - 1],
                "p99_ms": ordered[math.ceil(samples * 0.99) - 1],
                "max_ms": max(ordered),
                "latencies_ms": durations,
            }
        finally:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/tmp"))
    parser.add_argument("--samples", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 100:
        parser.error("Use ao menos 100 amostras")
    result = measure(args.directory, args.samples)
    print(json.dumps({k: v for k, v in result.items() if k != "latencies_ms"}, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
