"""Conclusão fim a fim: conversas do dataset pelo agente real, sob a API instável.

Extrator e conversador reais (OpenRouter), cadeia de cotação real contra a API local,
ingestão com janela de silêncio. O lead do dataset nunca informa a data de vigência;
quando o agente a pede, o harness responde com uma data fixa. Se o dataset acaba sem
cotação, o harness pede o plano Completo. Turnos sintéticos são contados à parte.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx

from agent.graph import TurnConfig
from agent.nodes.converse import Converser
from agent.nodes.extract import SlotExtractor
from application.llm import LLMRequest, LLMResponse
from application.ports import SystemClock
from domain.messages import InboundMessage, Intent, OutboundMessage, PedirDado
from domain.quote import Declined
from infrastructure.llm.budget import BudgetedLLMClient
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.privacy import PrivacyRedactor
from infrastructure.wiring import SalesStack, open_sales_stack
from interfaces.replay import dataset_path, read_rows
from tests.fakes import CANONICAL_OBJECTIONS

# turno (total, extração, fala, cotação), LLM (timeout HTTP, teto por chamada), tokens
SCENARIOS: dict[str, tuple[tuple[float, float, float, float], tuple[float, float], int]] = {
    "antes": ((6.0, 2.5, 3.0, 3.5), (2.0, 2.5), 4000),  # configuração da tarefa 8
    "medicao": ((120.0, 30.0, 30.0, 3.5), (30.0, 30.0), 10**7),  # sem corte
    # recalibrado pelo piloto sem corte (D-034): tetos acima do p99 de cada etapa
    "depois": ((10.0, 3.5, 4.5, 3.5), (4.5, 4.5), 16000),
}
START = "Pode começar em 15/10/2026."
PLAN = "Pode cotar o plano Completo."
REASONS = {
    "cotacao_esgotada": "indisponibilidade",
    "prazo_do_turno": "prazo",
    "linguagem_indisponivel": "llm_indisponivel",
    "limite_de_tokens": "limite_de_tokens",
    "documento_recebido": "midia",
    "midia_nao_resolvida": "midia",
}


class ObservedClient:
    def __init__(self, inner: OpenRouterLLMClient) -> None:
        self._inner = inner
        self.latency: dict[str, list[float]] = defaultdict(list)
        self.cost = Decimal(0)
        self.calls = self.failures = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        try:
            response = await self._inner.complete(request)
        except Exception:
            self.failures += 1
            raise
        self.latency[str(request.role)].append(response.latency_ms)
        self.cost += response.cost or 0
        return response


def percentiles(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def rank(p: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, int(-(-p * len(ordered) // 1)) - 1))]

    return {
        "n": len(ordered),
        "p50": rank(0.50),
        "p95": rank(0.95),
        "p99": rank(0.99),
        "max": ordered[-1],
    }


def bursts(rows: Sequence[Mapping[str, object]]) -> list[list[Mapping[str, object]]]:
    groups: list[list[Mapping[str, object]]] = []
    current: list[Mapping[str, object]] = []
    for row in sorted(rows, key=lambda item: int(str(item["message_index"]))):
        if row["sender_role"] == "lead":
            current.append(row)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def asks_start(reply: OutboundMessage | None) -> bool:
    return (
        reply is not None
        and isinstance(reply.payload, PedirDado)
        and reply.payload.slot == "data_inicio"
    )


async def run_conversation(
    stack: SalesStack, replied: dict[str, asyncio.Event], rows: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    conversation = str(rows[0]["conversation_id"])
    event = replied[conversation]
    position = max(int(str(row["message_index"])) for row in rows) + 1
    turns = synthetic = 0
    walls: list[float] = []
    quoted = objection_sent = False
    final: OutboundMessage | None = None
    reply: OutboundMessage | None = None

    async def send(messages: list[tuple[str, str, int]]) -> OutboundMessage | None:
        nonlocal turns
        event.clear()
        for kind, body, index in messages:
            await stack.ingestor.ingest(
                InboundMessage(
                    "replay", conversation, conversation, kind, body,  # type: ignore[arg-type]
                    f"{conversation}:{index}", index,
                )
            )
        start = time.monotonic()
        await asyncio.wait_for(event.wait(), timeout=180)
        walls.append(time.monotonic() - start)
        turns += 1
        return stack.session.latest_response(conversation)

    async def synthetic_turn(text: str) -> OutboundMessage | None:
        nonlocal position, synthetic
        synthetic += 1
        position += 1
        return await send([("text", text, position)])

    def settle(message: OutboundMessage | None) -> bool:
        nonlocal quoted, final
        if message is None:
            return False
        quoted = quoted or message.intent is Intent.APRESENTAR_COTACAO
        if message.intent in (Intent.RECUSAR, Intent.ESCALAR):
            final = message
            return True
        return False

    try:
        for burst in bursts(rows):
            objection_sent = objection_sent or any(
                str(row["message_body"]).split("...")[0] in CANONICAL_OBJECTIONS for row in burst
            )
            reply = await send(
                [
                    (
                        str(row["message_type"]),
                        str(row["message_body"]),
                        int(str(row["message_index"])),
                    )
                    for row in burst
                ]
            )
            while asks_start(reply) and synthetic < 3:
                reply = await synthetic_turn(START)
            if settle(reply):
                break
        for _ in range(3):
            if final is not None or quoted:
                break
            if isinstance(reply.payload if reply else None, PedirDado) and not asks_start(reply):
                break
            reply = await synthetic_turn(START if asks_start(reply) else PLAN)
            settle(reply)
        error = None
    except Exception as caught:  # a conversa conta como falha de execução, não some
        error = type(caught).__name__
    return {
        "conversation_id": conversation,
        "cotada": quoted,
        "final": final.intent.value if final else None,
        "recusa_origem": final.payload.origem
        if final is not None and isinstance(final.payload, Declined)
        else None,
        "turnos": turns,
        "sinteticos": synthetic,
        "objecao_enviada": objection_sent,
        "espera_s": walls,
        "erro": error,
    }


def classify(result: Mapping[str, object], motivo: str | None) -> str:
    if result["erro"]:
        return "erro_execucao"
    if result["cotada"]:
        return "cotada"
    if result["final"] == Intent.RECUSAR.value:
        return "recusa_regra" if result["recusa_origem"] == "regra_local" else "recusa_api"
    if result["final"] == Intent.ESCALAR.value:
        return REASONS.get(motivo or "", f"escalada:{motivo}")
    return "sem_cotacao"


async def measure(args: argparse.Namespace) -> dict[str, object]:
    turn, (timeout, ceiling), tokens = SCENARIOS[args.scenario]
    config = replace(LLMConfig.from_env(), timeout_seconds=timeout, budget_seconds=ceiling)
    turn_config = TurnConfig(*turn)
    rows = read_rows(args.dataset)
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["conversation_id"])].append(row)
    sample = random.Random(args.seed).sample(sorted(grouped), args.sample)
    database = Path(args.database)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    clock = SystemClock()
    replied = {conversation: asyncio.Event() for conversation in sample}
    semaphore = asyncio.Semaphore(args.concurrency)
    async with (
        httpx.AsyncClient(base_url=args.quote_url) as quote_client,
        httpx.AsyncClient() as llm_http,
    ):
        observed = ObservedClient(OpenRouterLLMClient(llm_http, config, clock))
        budgeted = BudgetedLLMClient(observed, tokens)
        extractor = SlotExtractor(budgeted, PrivacyRedactor(), default_budget=ceiling)
        started = time.monotonic()
        async with open_sales_stack(
            database,
            quote_client=quote_client,
            extractor=extractor,
            converser=Converser(budgeted),
            clock=clock,
            sleep=asyncio.sleep,
            rng=random.random,
            turn_config=turn_config,
            after_reply=lambda conversation: replied[conversation].set(),
        ) as stack:

            async def bounded(conversation: str) -> dict[str, object]:
                async with semaphore:
                    return await run_conversation(stack, replied, grouped[conversation])

            results = await asyncio.gather(*(bounded(item) for item in sample))
        elapsed = time.monotonic() - started
        used = {item: budgeted.tokens_used(item) for item in sample}

    connection = sqlite3.connect(database)
    try:
        motivos = dict(
            connection.execute(
                "SELECT conversation_id, motivo FROM handoffs ORDER BY criado_em, rowid"
            ).fetchall()
        )
        events = connection.execute(
            "SELECT trace_id, etapa, latencia_ms FROM turn_events"
        ).fetchall()
        objection_events = connection.execute(
            "SELECT trace_id, status FROM turn_events WHERE etapa='objecao'"
        ).fetchall()
        logical = connection.execute(
            "SELECT latencia_ms, status FROM quote_attempts WHERE tentativa=0"
        ).fetchall()
    finally:
        connection.close()

    stages: dict[str, list[float]] = defaultdict(list)
    per_turn: dict[str, float] = defaultdict(float)
    for trace_id, etapa, latency in events:
        if etapa in ("guardrail", "objecao"):
            continue
        stages[etapa].append(latency)
        per_turn[trace_id] += latency
    totals = list(per_turn.values())
    outcomes = Counter(
        classify(item, motivos.get(str(item["conversation_id"]))) for item in results
    )
    routed = {trace_id.rsplit(":", 2)[0] for trace_id, _ in objection_events}
    sent = {str(item["conversation_id"]) for item in results if item["objecao_enviada"]}
    return {
        "cenario": args.scenario,
        "turno": dict(zip(("total", "extracao", "fala", "cotacao"), turn, strict=True)),
        "llm": {"timeout_http_s": timeout, "teto_chamada_s": ceiling, "tokens_conversa": tokens},
        "amostra": len(sample),
        "semente": args.seed,
        "concorrencia": args.concurrency,
        "duracao_s": round(elapsed, 1),
        "desfechos": dict(outcomes.most_common()),
        "conclusao": outcomes["cotada"] / len(sample),
        "etapas_ms": {name: percentiles(values) for name, values in sorted(stages.items())},
        "turno_ms": percentiles(totals),
        "turnos_acima_de": {
            f"{limit}s": sum(total > limit * 1000 for total in totals) for limit in (6, 8, 10)
        },
        "espera_lead_s": percentiles([w for item in results for w in item["espera_s"]]),  # type: ignore[attr-defined]
        "cotacao_logica_ms": percentiles([row[0] for row in logical]),
        "cotacao_status": dict(Counter(row[1] for row in logical)),
        "llm_ms": {role: percentiles(values) for role, values in observed.latency.items()},
        "llm_chamadas": observed.calls,
        "llm_falhas": observed.failures,
        "custo_conhecido_usd": str(observed.cost),
        "tokens_conversa": percentiles(list(used.values())),
        "objecao": {
            "conversas_com_objecao_enviada": len(sent),
            "roteadas_ao_no": len(sent & routed),
            "eventos_por_fonte": dict(Counter(status for _, status in objection_events)),
        },
        "turnos_sinteticos": sum(int(str(item["sinteticos"])) for item in results),
        "turnos_totais": sum(int(str(item["turnos"])) for item in results),
        "conversas": [
            {
                "conversation_id": item["conversation_id"],
                "desfecho": classify(item, motivos.get(str(item["conversation_id"]))),
                "turnos": item["turnos"],
                "sinteticos": item["sinteticos"],
                "tokens": used[str(item["conversation_id"])],
            }
            for item in results
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--quote-url", default="http://127.0.0.1:18010")
    parser.add_argument("--dataset", type=Path, default=dataset_path())
    parser.add_argument("--database", default="/tmp/autoseguro-e2e.sqlite")
    parser.add_argument("--sample", type=int, default=150)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(measure(args))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    summary = {key: value for key, value in report.items() if key != "conversas"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
