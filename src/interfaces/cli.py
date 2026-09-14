"""Conversa com o agente no terminal (tarefa 12).

Adapter sobre os mesmos casos de uso do replay: a linha digitada vira `InboundMessage` e o
`OutboundMessage` volta renderizado. Nenhuma regra de negócio vive aqui — slots, políticas
e cotação são do grafo; o `--trace` só formata o que `InspectConversation` lê do turno.

    uv run --env-file .env python -m interfaces.cli --trace
    uv run --env-file .env python -m interfaces.cli --conversation cli-1a2b3c4d
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import uuid4

from application.external import ConfigurationError, StartupCheckError
from domain.messages import InboundMessage, MessageType
from infrastructure.wiring import SalesStack, open_live_stack
from interfaces.conversation_report import render_turn
from interfaces.rendering import render_outbound

HELP = """Comandos:
  /imagem <arquivo>    foto; resolvida pelo modelo de mídia quando o arquivo existe
  /audio <arquivo>     áudio; a transcrição vira texto que pede confirmação
  /documento <nome>    documento; nunca sai do processo e segue para uma pessoa
  /encerrar            encerra a conversa: slots e estado do grafo são apagados
  /sair                sai sem encerrar; retome com --conversation <id>
  /ajuda               esta ajuda"""

# Tradução de comando para o envelope; o que cada tipo provoca é decisão do grafo.
_MEDIA: dict[str, tuple[MessageType, str]] = {
    "/imagem": ("image", "[imagem]"),
    "/audio": ("audio", "[audio]"),
    "/documento": ("document", "[documento]"),
}


def inbound_message(conversation_id: str, index: int, text: str) -> InboundMessage | None:
    command, _, argument = text.partition(" ")
    kind: MessageType = "text"
    body, ref = text, None
    if command in _MEDIA:
        kind, marker = _MEDIA[command]
        body = f"{marker} {Path(argument).name}".strip()
        # Documento nunca vai a provedor; imagem e áudio levam o caminho do arquivo.
        ref = (argument.strip() or None) if kind != "document" else None
    elif command.startswith("/"):
        return None
    return InboundMessage(
        "cli",
        conversation_id,
        conversation_id,
        kind,
        body,
        f"{conversation_id}:{index}",
        index,
        media_ref=ref,
    )


async def chat(
    stack: SalesStack,
    conversation_id: str,
    lines: AsyncIterator[str],
    write: Callable[[str], None],
    *,
    trace: bool = False,
) -> None:
    index = await stack.ingestor.next_index(conversation_id)
    if index:
        reports = await stack.inspector.execute(conversation_id)
        recovered = reports[-1].slots if reports else ()
        known = ", ".join(f"{slot.nome} {slot.valor}" for slot in recovered)
        write(f"Retomando {conversation_id}: {known or 'nenhum slot coletado'}.")
    async for line in lines:
        text = line.strip()
        if not text:
            continue
        if text == "/sair":
            return
        if text == "/ajuda":
            write(HELP)
            continue
        if text == "/encerrar":
            await stack.session.close(conversation_id)
            write(f"Conversa {conversation_id} encerrada: slots e estado do grafo apagados.")
            return
        message = inbound_message(conversation_id, index, text)
        if message is None:
            write("Comando desconhecido; /ajuda lista os comandos.")
            continue
        index += 1
        await stack.ingestor.ingest(message)
        await stack.ingestor.wait_idle()
        reply = stack.session.latest_response(conversation_id)
        write(f"Agente: {render_outbound(reply)}" if reply else "Agente: (sem resposta)")
        if trace:
            reports = await stack.inspector.execute(conversation_id)
            if reports:
                write("\n".join(render_turn(len(reports), reports[-1])))


async def _stdin() -> AsyncIterator[str]:
    while True:
        try:
            yield await asyncio.to_thread(input, "Você: ")
        except EOFError:
            return


async def _run(args: argparse.Namespace, conversation_id: str) -> None:
    async with open_live_stack(args.database, quote_url=args.quote_url) as stack:
        print(f"Conversa {conversation_id}. /ajuda lista os comandos.")
        await chat(stack, conversation_id, _stdin(), print, trace=args.trace)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conversa com o agente no terminal.")
    parser.add_argument("--conversation", help="retoma uma conversa existente pelo id")
    parser.add_argument(
        "--trace", action="store_true", help="slots, políticas e tentativas de cada turno"
    )
    parser.add_argument("--database", type=Path, default=Path("autoseguro.sqlite"))
    parser.add_argument("--quote-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    conversation_id = args.conversation or f"cli-{uuid4().hex[:8]}"
    try:
        asyncio.run(_run(args, conversation_id))
    except (StartupCheckError, ConfigurationError, ValueError) as error:
        print(f"Não foi possível iniciar: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    print(f"Para retomar: python -m interfaces.cli --conversation {conversation_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
