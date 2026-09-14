"""Log de execução completa: uma conversa real do dataset pelo agente, depois o que o
comando de inspeção lê do banco que a execução deixou.

A conversa roda pelo mesmo harness da medição fim a fim (extrator e conversador reais,
cadeia real contra a API local). O corpo do documento é exatamente a saída de
`python -m interfaces.trace --conversation <id> --database <banco>`.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
from pathlib import Path

from infrastructure.llm.config import LLMConfig
from infrastructure.wiring import conversation_inspector
from interfaces.conversation_report import render_conversation
from interfaces.replay import dataset_path, read_rows
from scripts.measure_end_to_end import SCENARIOS, measure

API_COMMAND = (
    "cd ../namastex-fde-challenge/quote-service && {env} uv run --with fastapi "
    "--with uvicorn uvicorn app.main:app --host 127.0.0.1 --port {port}"
)


def br(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def synthetic_turns(conversation: str, traces: list[str], dataset: Path) -> list[int]:
    """Turnos cuja mensagem veio do harness (data de vigência, pedido de plano)."""
    last = max(
        int(str(row["message_index"]))
        for row in read_rows(dataset)
        if row["conversation_id"] == conversation
    )
    return [n for n, trace in enumerate(traces, 1) if int(trace.rsplit(":", 1)[1]) > last]


async def generate(args: argparse.Namespace) -> str:
    run = argparse.Namespace(
        scenario="p999",
        quote_url=args.quote_url,
        dataset=args.dataset,
        database=args.database,
        sample=1,
        seed=0,
        concurrency=1,
        conversations=args.conversation,
        ineligible=False,
    )
    report = await measure(run)
    async with conversation_inspector(Path(args.database)) as inspector:
        reports = await inspector.execute(args.conversation)
    body = render_conversation(args.conversation, reports)
    synthetic = synthetic_turns(args.conversation, [r.trace_id for r in reports], args.dataset)
    turn, (timeout, ceiling), tokens = SCENARIOS["p999"]
    llm = LLMConfig.from_env()
    port = args.quote_url.rsplit(":", 1)[1]
    command = (
        "AUTOSEGURO_DATASET=<conversations.parquet> uv run --env-file .env python -m "
        f"scripts.execution_log --conversation {args.conversation} "
        f"--quote-url {args.quote_url} --quote-env {shlex.quote(args.quote_env)} "
        f"--title {shlex.quote(args.title)} --output {args.output}"
    )
    check = report["verificacao_cotacoes"]
    outcomes = report["desfechos"]
    assert isinstance(check, dict) and isinstance(outcomes, dict)
    verified = (
        f"{check['consistentes_com_a_tabela']} de {check['cotacoes_verificadas']} consistente"
        if check["cotacoes_verificadas"]
        else "não se aplica, nenhuma cotação foi apresentada"
    )
    header = [
        f"# {args.title}",
        "",
        f"Conversa real do dataset, `{args.conversation}`, pelo agente completo. O corpo abaixo",
        "é a saída do comando de inspeção sobre o banco que a execução deixou:",
        "",
        "```bash",
        f"uv run python -m interfaces.trace --conversation {args.conversation} "
        f"--database {args.database}",
        "```",
        "",
        "## Como reproduzir",
        "",
        "```bash",
        "# 1. API do desafio numa instância nova: o sorteio começa na primeira /quote",
        API_COMMAND.format(env=args.quote_env, port=port),
        "# 2. a conversa pelo agente, e este documento",
        command,
        "```",
        "",
        "O que se repete e o que não:",
        "",
        f"- **Sorteio da API**: fixo por `{args.quote_env}`, desde que a instância seja nova e",
        "  esta seja a única conversa contra ela.",
        "- **Hedge**: depende de relógio de parede. Ele dispara quando a chamada não voltou em",
        "  100 ms; falha que volta antes disso propaga na hora e fica com o retry. Estável na",
        "  prática, não por construção.",
        "- **Texto do LLM**: extrator e conversador são modelos reais; a redação da fala e, em",
        "  raros casos, um slot podem variar entre execuções. Preço, franquia, carência e",
        "  pro-rata nunca variam: vêm do payload da `/quote` por template.",
        "",
        "## Configuração",
        "",
        f"- Extrator `{llm.extractor_model}`, conversador `{llm.conversation_model}` (OpenRouter)",
        f"- Turno de {br(turn[0])} s; teto por chamada de LLM de {br(ceiling)} s com um retry"
        f" (D-038); cotação até {br(turn[3])} s",
        f"- Limite de {tokens:,} tokens por conversa; timeout HTTP do LLM de {br(timeout)} s"
        .replace(",", "."),
        "",
        "## Resultado",
        "",
        f"- Desfecho: `{next(iter(outcomes))}`, em {len(reports)} turnos",
        "- Turnos com mensagem do harness, não do dataset (o dataset não traz data de "
        f"vigência): {', '.join(map(str, synthetic)) or 'nenhum'}",
        f"- Cotação conferida contra a tabela no perfil real do lead: {verified}",
        "",
    ]
    return "\n".join(header) + "\n" + body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation", required=True)
    parser.add_argument("--quote-url", required=True)
    parser.add_argument("--quote-env", default="QUOTE_SEED=1 QUOTE_FAILURE_RATE=0.20")
    parser.add_argument("--title", default="Execução completa")
    parser.add_argument("--dataset", type=Path, default=dataset_path())
    parser.add_argument("--database", default="/tmp/autoseguro-execucao.sqlite")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(asyncio.run(generate(args)), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
