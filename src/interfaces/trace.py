from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from infrastructure.wiring import conversation_inspector, trace_inspector
from interfaces.conversation_report import render_conversation


async def _conversation(database: Path, conversation_id: str) -> str:
    async with conversation_inspector(database) as inspector:
        reports = await inspector.execute(conversation_id)
    return render_conversation(conversation_id, reports) if reports else "Conversa não encontrada."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspeciona a linha do tempo de uma cotação ou de uma conversa inteira."
    )
    parser.add_argument("trace_id", nargs="?")
    parser.add_argument("--conversation", help="conversa inteira, turno a turno, em markdown")
    parser.add_argument("--database", type=Path, default=Path("autoseguro.sqlite"))
    args = parser.parse_args(argv)
    if (args.trace_id is None) == (args.conversation is None):
        parser.error("informe um trace_id ou --conversation")
    try:
        if args.conversation:
            output = asyncio.run(_conversation(args.database, args.conversation))
        else:
            with trace_inspector(args.database) as inspector:
                output = asyncio.run(inspector.execute(args.trace_id))
    except Exception:
        print("Não foi possível consultar o trace.", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
