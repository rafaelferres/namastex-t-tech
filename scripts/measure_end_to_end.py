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
import re
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from agent.graph import TurnConfig
from agent.nodes.converse import Converser
from agent.nodes.extract import SlotExtractor
from application.llm import LLMRequest, LLMResponse
from application.ports import SystemClock
from domain.acceptance import AcceptanceRules
from domain.messages import ApresentarCotacao, InboundMessage, Intent, OutboundMessage, PedirDado
from domain.quote import Declined, Quote, QuoteRequest
from infrastructure.llm.budget import BudgetedLLMClient
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.privacy import PrivacyRedactor
from infrastructure.wiring import SalesStack, open_sales_stack
from interfaces.rendering import render_outbound
from interfaces.replay import dataset_path, read_rows
from tests.fakes import CANONICAL_OBJECTIONS
from tests.golden.harness import cases_from_rows
from tests.regression.oracle import negative_cases

# turno (total, extração, fala, cotação), LLM (timeout HTTP, teto por chamada), tokens
SCENARIOS: dict[str, tuple[tuple[float, float, float, float], tuple[float, float], int]] = {
    "antes": ((6.0, 2.5, 3.0, 3.5), (2.0, 2.5), 4000),  # configuração da tarefa 8
    "medicao": ((120.0, 30.0, 30.0, 3.5), (30.0, 30.0), 10**7),  # sem corte
    # recalibrado pelo piloto sem corte (D-034): tetos acima do p99 de cada etapa
    "depois": ((10.0, 3.5, 4.5, 3.5), (4.5, 4.5), 16000),
    # D-038: teto no p99.9 por chamada, um retry, turno que comporta o caminho no p99.9
    "p999": ((18.0, 7.0, 7.0, 3.5), (7.0, 7.0), 16000),
}
START = "Pode começar em 15/10/2026."
START_DATE = date(2026, 10, 15)
PLAN = "Pode cotar o plano Completo."
# Gabarito do CEP: o gerador escreve "cep 99999-999" em toda conversa (2.500 de 2.500).
TRUE_CEP = re.compile(r"\bcep\s+(\d{5}-?\d{3})\b", re.IGNORECASE)
REASONS = {
    "cotacao_esgotada": "indisponibilidade",
    "prazo_do_turno": "prazo",
    "linguagem_indisponivel": "llm_indisponivel",
    "limite_de_tokens": "limite_de_tokens",
    "documento_recebido": "documento",
    "midia_nao_resolvida": "audio_sem_texto",
}
# Mesmo padrão do teste de prompt: fala do agente pedindo arquivo (o texto não é gravado).
ASKS_FOR_FILE = re.compile(
    r"\b(?:envi|mand|anex|encaminh)\w*\b.{0,30}\b(?:documento|foto|imagem|cpf|cnh|crlv)",
    re.IGNORECASE,
)


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
    turns = synthetic = asked_for_file = 0
    walls: list[float] = []
    quoted = objection_sent = False
    final: OutboundMessage | None = None
    reply: OutboundMessage | None = None
    shown: dict[str, object] | None = None

    async def send(messages: list[tuple[str, str, int]]) -> OutboundMessage | None:
        nonlocal turns, asked_for_file
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
        reply = stack.session.latest_response(conversation)
        if (
            reply is not None
            and reply.intent is not Intent.ESCALAR
            and ASKS_FOR_FILE.search(render_outbound(reply))
        ):
            asked_for_file += 1
        return reply

    async def synthetic_turn(text: str) -> OutboundMessage | None:
        nonlocal position, synthetic
        synthetic += 1
        position += 1
        return await send([("text", text, position)])

    def settle(message: OutboundMessage | None) -> bool:
        nonlocal quoted, final, shown
        if message is None:
            return False
        quoted = quoted or message.intent is Intent.APRESENTAR_COTACAO
        if shown is None and isinstance(message.payload, ApresentarCotacao):
            # O que o lead viu, para conferir depois contra a tabela no perfil real.
            quote = message.payload.quote
            rata = quote.primeiro_pagamento_pro_rata
            shown = {
                "plano_id": quote.plano_id,
                "premio_mensal": str(quote.premio_mensal),
                "franquia": str(quote.franquia),
                "pro_rata": str(rata.valor_primeiro_pagamento) if rata else None,
                "carencia_dias": quote.carencia.dias,
                "menciona_carencia": f"Carência de {quote.carencia.dias} dias"
                in render_outbound(message),
            }
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
        "pedidos_de_arquivo": asked_for_file,
        "espera_s": walls,
        "erro": error,
        "cotacao": shown,
    }


async def table_quote(client: httpx.AsyncClient, request: QuoteRequest) -> Quote | Declined:
    """A /quote é a tabela; a instabilidade é sorteada, então insiste até ter resposta."""
    for _ in range(12):
        try:
            response = await client.post("/quote", json=request.to_payload(), timeout=10.0)
        except httpx.TimeoutException:
            continue
        if response.status_code == 200:
            return Quote.from_api(response.json())
        if response.status_code == 422:
            return Declined(str(response.json()))
    raise RuntimeError("Tabela indisponível após 12 tentativas")


async def verify_quotes(
    client: httpx.AsyncClient,
    grouped: Mapping[str, Sequence[Mapping[str, object]]],
    results: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Cotação que o lead viu contra a tabela no perfil REAL (gabarito do dataset).

    Pega extração errada também: perfil trocado dá preço de outro perfil.
    """
    checked = consistent = applicable = mentioned = 0
    divergent: list[dict[str, object]] = []
    for item in results:
        shown = item["cotacao"]
        if not isinstance(shown, dict):
            continue
        rows = grouped[str(item["conversation_id"])]
        (case,) = cases_from_rows(rows)
        lead = [
            str(row["message_body"])
            for row in sorted(rows, key=lambda row: int(str(row["message_index"])))
            if row["sender_role"] == "lead"
        ]
        cep = next((match[1] for text in lead if (match := TRUE_CEP.search(text))), None)
        request = QuoteRequest(
            str(shown["plano_id"]),
            case.idade,
            int(case.veiculo_texto.rsplit(" ", 1)[1]),
            cep=cep,
            data_inicio=START_DATE,
        )
        expected = await table_quote(client, request)
        checked += 1
        if isinstance(expected, Declined):
            divergent.append({"conversation_id": item["conversation_id"], "campos": ["recusa"]})
        else:
            rata = expected.primeiro_pagamento_pro_rata
            truth = {
                "premio_mensal": expected.premio_mensal,
                "franquia": expected.franquia,
                "pro_rata": rata.valor_primeiro_pagamento if rata else None,
            }
            fields = [
                name
                for name, value in truth.items()
                if (Decimal(str(shown[name])) if shown[name] is not None else None) != value
            ]
            consistent += not fields
            if fields:
                divergent.append({"conversation_id": item["conversation_id"], "campos": fields})
        if int(str(shown["carencia_dias"])) > 0:
            applicable += 1
            mentioned += bool(shown["menciona_carencia"])
    return {
        "cotacoes_verificadas": checked,
        "consistentes_com_a_tabela": consistent,
        "divergentes": divergent,
        "carencia_aplicavel": applicable,
        "carencia_mencionada": mentioned,
    }


def divergence(decisions: Sequence[tuple[str, str | None]]) -> dict[str, object]:
    """Sugestão do conversador contra a decisão da política, nos turnos em que ele falou."""
    return {
        "turnos_com_fala": len(decisions),
        "modelo_sugeriu": sum(hint is not None for _, hint in decisions),
        "politica_escalou": sum(status != "segue" for status, _ in decisions),
        "modelo_sugeriu_politica_nao": sum(
            hint is not None and status == "segue" for status, hint in decisions
        ),
        "politica_escalou_modelo_nao": sum(
            hint is None and status != "segue" for status, hint in decisions
        ),
        "motivos_diferentes": sum(
            hint is not None and status not in ("segue", hint) for status, hint in decisions
        ),
        "sugestoes": dict(Counter(hint for _, hint in decisions if hint)),
        "decisoes": dict(Counter(status for status, _ in decisions)),
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
    if args.conversations:
        sample = args.conversations.split(",")
    elif args.ineligible:
        # Oráculo negativo: as 751 que o vendedor humano cotou sem poder.
        async with httpx.AsyncClient(base_url=args.quote_url) as planos:
            rules = AcceptanceRules.from_api((await planos.get("/planos")).json())
        negatives = negative_cases(cases_from_rows(rows), rules)
        sample = sorted(case.conversation_id for case in negatives)
    else:
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
        verification = await verify_quotes(quote_client, grouped, results)

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
        decisions = connection.execute(
            "SELECT status, sugestao FROM turn_events WHERE etapa='decisao'"
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
        "divergencia": divergence(decisions),
        "apresentaram_preco": sum(item["cotacao"] is not None for item in results),
        "verificacao_cotacoes": verification,
        "falas_pedindo_arquivo": sum(int(str(item["pedidos_de_arquivo"])) for item in results),
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
    parser.add_argument("--conversations", help="ids separados por vírgula, no lugar da amostra")
    parser.add_argument("--ineligible", action="store_true", help="as 751 do oráculo negativo")
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
