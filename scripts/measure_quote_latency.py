"""Medição real; execute contra API com FAILURE_RATE e SLOW_RATE iguais a zero."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from time import perf_counter

import httpx


async def measure(url: str, samples: int, warmup: int) -> dict[str, object]:
    durations = []
    async with httpx.AsyncClient(base_url=url, timeout=10, trust_env=False) as client:
        for index in range(warmup + samples):
            start = perf_counter()
            response = await client.post(
                "/quote",
                json={
                    "plano_id": "completo",
                    "idade": 30,
                    "veiculo_ano": datetime.now().year,
                },
            )
            elapsed = (perf_counter() - start) * 1000
            if response.status_code != 200:
                raise RuntimeError(f"Amostra inválida: HTTP {response.status_code}")
            if index >= warmup:
                durations.append(elapsed)
    ordered = sorted(durations)
    return {
        "samples": samples,
        "warmup": warmup,
        "url": url,
        "median_ms": statistics.median(ordered),
        "p95_ms": ordered[math.ceil(samples * 0.95) - 1],
        "p99_ms": ordered[math.ceil(samples * 0.99) - 1],
        "max_ms": max(ordered),
        "latencies_ms": durations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    parser.add_argument("--samples", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 100 or args.warmup < 0:
        parser.error("Use ao menos 100 amostras e warmup não negativo")
    result = asyncio.run(measure(args.url, args.samples, args.warmup))
    for key in ("samples", "warmup", "median_ms", "p95_ms", "p99_ms", "max_ms"):
        print(f"{key}: {result[key]}")
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
