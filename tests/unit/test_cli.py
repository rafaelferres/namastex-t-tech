"""CLI de conversa: adapter sobre os casos de uso, sem regra de negócio própria."""

from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.graph import TurnConfig
from agent.nodes.converse import ConversationResult
from agent.nodes.extract import ExtractionResult
from agent.schemas.slots import Slots
from infrastructure.wiring import SalesStack, open_sales_stack
from interfaces import cli
from tests.fakes import FakeClock


def slots(**values: object) -> Slots:
    return Slots.model_validate(
        {
            key: {"valor": value, "status": "informado", "proveniencia": "digitado"}
            for key, value in values.items()
        }
    )


FULL = {"idade": 30, "veiculo_ano": 2020, "data_inicio": "2026-09-16"}


@asynccontextmanager
async def stack_for(
    path: Path,
    plans_payload: dict[str, Any],
    quote_payload: dict[str, Any],
    *,
    extracted: list[Slots],
    spoken: list[ConversationResult],
) -> AsyncIterator[tuple[SalesStack, AsyncMock]]:
    clock = FakeClock()

    async def sleep(delay: float) -> None:
        if delay >= 1:
            await asyncio.Future()
        clock.elapsed += delay
        await asyncio.sleep(0)

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/planos":
            return httpx.Response(200, json=plans_payload)
        assert json.loads(request.content)["plano_id"] == "completo"
        return httpx.Response(200, json=quote_payload)

    results = [ExtractionResult(item) for item in extracted]
    extractor = AsyncMock(extract=AsyncMock(side_effect=results))
    converser = AsyncMock(converse=AsyncMock(side_effect=spoken))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(api), base_url="https://quote.test"
    ) as client:
        async with open_sales_stack(
            path,
            quote_client=client,
            extractor=extractor,
            converser=converser,
            clock=clock,
            sleep=sleep,
            rng=lambda: 0.5,
            turn_config=TurnConfig(),
            verify=False,
        ) as stack:
            yield stack, extractor


async def lines(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


def test_cli_is_an_adapter_without_business_imports():
    tree = ast.parse(Path(cli.__file__).read_text())
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "agent.graph", "agent.nodes", "agent.templates", "domain.handoff", "domain.acceptance",
        "domain.quote", "infrastructure.quote", "infrastructure.persistence", "sqlite3",
    )
    assert not {name for name in imported if name.startswith(forbidden)}


@pytest.mark.asyncio
async def test_full_conversation_reaches_the_quote_and_trace_shows_the_ladder(
    tmp_path, plans_payload, quote_payload
):
    out: list[str] = []
    async with stack_for(
        tmp_path / "agent.sqlite",
        plans_payload,
        quote_payload,
        extracted=[slots(idade=30), slots(**FULL), slots(**FULL)],
        spoken=[
            ConversationResult("Qual plano você prefere?", None),
            ConversationResult("", None, "completo"),
        ],
    ) as (stack, _):
        await cli.chat(
            stack,
            "conv-cli",
            lines("Tenho 30 anos", "Onix 2020, começando em 16/09/2026", "Quero o Completo"),
            out.append,
            trace=True,
        )
    text = "\n".join(out)
    assert "ano-modelo do veículo" in text
    assert "Qual plano você prefere?" in text
    assert "Mensalidade: R$" in text
    assert "| idade | 30 | informado | digitado |" in text
    assert "| 1 | quoted | 200 |" in text
    assert "Aceitação: dentro das regras" in text


@pytest.mark.asyncio
async def test_resume_by_conversation_recovers_the_slots(tmp_path, plans_payload, quote_payload):
    path = tmp_path / "agent.sqlite"
    first: list[str] = []
    async with stack_for(
        path, plans_payload, quote_payload, extracted=[slots(idade=30)], spoken=[]
    ) as (stack, _):
        await cli.chat(stack, "conv-cli", lines("Tenho 30 anos"), first.append)
    again: list[str] = []
    async with stack_for(
        path,
        plans_payload,
        quote_payload,
        extracted=[slots(**FULL)],
        spoken=[ConversationResult("Qual plano você prefere?", None)],
    ) as (stack, extractor):
        await cli.chat(
            stack, "conv-cli", lines("Onix 2020, começando em 16/09/2026"), again.append
        )
    assert "idade 30" in again[0]
    # O grafo retomou do checkpointer: o extrator recebeu a idade da sessão anterior.
    assert extractor.extract.call_args.args[1].idade.valor == 30
    assert "Qual plano você prefere?" in "\n".join(again)


@pytest.mark.asyncio
async def test_document_escalates_and_close_ends_the_conversation(
    tmp_path, plans_payload, quote_payload
):
    out: list[str] = []
    async with stack_for(
        tmp_path / "agent.sqlite",
        plans_payload,
        quote_payload,
        extracted=[slots(idade=30)],
        spoken=[],
    ) as (stack, _):
        await cli.chat(stack, "conv-cli", lines("/documento CNH.pdf", "/encerrar"), out.append)
        reports = await stack.inspector.execute("conv-cli")
    text = "\n".join(out)
    assert "pessoa da equipe" in text
    assert "encerrada" in text
    assert reports == ()  # encerrar apaga o estado do grafo
