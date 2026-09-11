from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from infrastructure.wiring import trace_inspector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspeciona a linha do tempo de uma cotação.")
    parser.add_argument("trace_id")
    parser.add_argument("--database", type=Path, default=Path("autoseguro.sqlite"))
    args = parser.parse_args(argv)
    try:
        with trace_inspector(args.database) as inspector:
            output = asyncio.run(inspector.execute(args.trace_id))
    except Exception:
        print("Não foi possível consultar o trace.", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
