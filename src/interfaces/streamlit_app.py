"""Console Streamlit (tarefa 14): quarto adapter, ao lado de CLI, replay e trace.

    uv sync --group console
    uv run --env-file .env streamlit run src/interfaces/streamlit_app.py

Monta widget, chama caso de uso e desenha o resultado. A conversa vive no checkpointer:
`st.session_state` guarda só o `thread_id`. Toda chamada async passa pela `AsyncBridge`.
O painel de trace é o mesmo relatório redigido do `--trace` da CLI.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import streamlit as st

from application.external import ConfigurationError, StartupCheckError
from application.inspect_conversation import TurnReport
from infrastructure.wiring import SalesStack, open_live_stack
from interfaces import evaluation
from interfaces.async_bridge import AsyncBridge
from interfaces.cli import inbound_message
from interfaces.conversation_report import render_turn
from interfaces.quote_api import ManagedQuoteApi, QuoteApiSettings
from interfaces.rendering import escape_dollar, render_outbound
from interfaces.replay import dataset_path

SERVICE_DIR = Path(
    os.environ.get("AUTOSEGURO_QUOTE_SERVICE", "../namastex-fde-challenge/quote-service")
)
# Parâmetros das rodadas do README: a avaliação ao vivo usa uma instância própria com eles.
README_API = QuoteApiSettings(failure_rate=0.20, slow_rate=0.10, seed=42)


@dataclass
class Console:
    """Recursos do processo, compartilhados entre reexecuções; nenhum estado de conversa."""

    bridge: AsyncBridge
    api: ManagedQuoteApi
    eval_api: ManagedQuoteApi
    workdir: Path
    stack: SalesStack | None = None
    exits: AsyncExitStack = field(default_factory=AsyncExitStack)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


async def _open_stack(console: Console) -> SalesStack:
    await console.exits.aclose()
    console.exits = AsyncExitStack()
    database = console.workdir / f"console-{uuid4().hex[:8]}.sqlite"
    return await console.exits.enter_async_context(
        open_live_stack(database, quote_url=console.api.url)
    )


def _build_console() -> Console:
    workdir = Path(tempfile.mkdtemp(prefix="autoseguro-console-"))
    console = Console(
        AsyncBridge(),
        ManagedQuoteApi(SERVICE_DIR, 18020),
        ManagedQuoteApi(SERVICE_DIR, 18021),
        workdir,
    )
    console.bridge.run(console.api.restart(QuoteApiSettings()))
    console.stack = console.bridge.run(_open_stack(console))
    return console


async def _send(stack: SalesStack, conversation_id: str, text: str) -> None:
    # A mesma tradução de linha em envelope da CLI; o que cada mensagem provoca é do grafo.
    index = await stack.ingestor.next_index(conversation_id)
    message = inbound_message(conversation_id, index, text)
    if message is not None:
        await stack.ingestor.ingest(message)
        await stack.ingestor.wait_idle()


st.set_page_config(page_title="AutoSeguro — console", layout="wide")

if not SERVICE_DIR.is_dir():
    st.error(f"API do desafio não encontrada em `{SERVICE_DIR}`. Defina AUTOSEGURO_QUOTE_SERVICE.")
    st.stop()
try:
    console: Console = st.cache_resource(_build_console)()
except (StartupCheckError, ConfigurationError, ValueError, RuntimeError) as error:
    st.error(f"Não foi possível iniciar: {error}")
    st.stop()
stack = console.stack
assert stack is not None

if "thread_id" not in st.session_state:
    st.session_state.thread_id = st.query_params.get("conversa") or f"console-{uuid4().hex[:8]}"
thread_id: str = st.session_state.thread_id

# --- controles -----------------------------------------------------------------------------
with st.sidebar:
    st.header("API de cotação")
    current = console.api.settings or QuoteApiSettings()
    rate = st.slider("Taxa de falha", 0.0, 1.0, current.failure_rate, 0.1)
    seed = st.number_input("QUOTE_SEED (0 = sem semente)", 0, 10_000, current.seed or 0)
    if st.button("Reiniciar a API com estes valores", use_container_width=True):
        with st.spinner("Reiniciando a API…"):
            console.bridge.run(
                console.api.restart(QuoteApiSettings(failure_rate=rate, seed=int(seed) or None))
            )
        st.rerun()
    st.caption(
        f"No ar: falha {current.failure_rate:.0%}, semente {current.seed or '—'}. "
        "Em 20%, o retry recupera; em 100%, a escada esgota e a conversa escala com snapshot. "
        "A mesma cotação no mesmo dia sai do cache, fora da escada: use um banco novo."
    )
    st.header("Conversa")
    st.code(thread_id, language=None)
    left, right = st.columns(2)
    if left.button("Nova conversa", use_container_width=True):
        st.session_state.thread_id = f"console-{uuid4().hex[:8]}"
        st.rerun()
    if right.button("Banco novo", use_container_width=True, help="Limpa o cache de cotação"):
        console.stack = console.bridge.run(_open_stack(console))
        st.session_state.thread_id = f"console-{uuid4().hex[:8]}"
        st.rerun()
    st.header("Injetar mídia")
    upload = st.file_uploader("Imagem", type=["jpg", "jpeg", "png", "webp"])
    if upload is not None and st.button("Enviar imagem", use_container_width=True):
        path = console.workdir / Path(upload.name).name
        path.write_bytes(upload.getvalue())
        with st.spinner("Agente respondendo…"):
            console.bridge.run(_send(stack, thread_id, f"/imagem {path}"))
    document = st.text_input("Documento", "CNH_frente.pdf")
    if st.button("Enviar documento", use_container_width=True):
        with st.spinner("Agente respondendo…"):
            console.bridge.run(_send(stack, thread_id, f"/documento {document}"))

sandbox, assessment = st.tabs(["Sandbox", "Avaliação"])

# --- sandbox -------------------------------------------------------------------------------
with sandbox:
    chat, trace = st.columns([2, 3], gap="large")
    with chat:
        history = st.container(height=640)
        text = st.chat_input("Mensagem do lead")
        if text:
            with st.spinner("Agente respondendo…"):
                console.bridge.run(_send(stack, thread_id, text))
        reports: tuple[TurnReport, ...] = console.bridge.run(stack.inspector.execute(thread_id))
        for report in reports:
            # O que foi persistido: a mensagem do lead já redigida na ingestão.
            history.chat_message("user").markdown(escape_dollar(report.entrada))
            if report.resposta is not None:
                history.chat_message("assistant").markdown(
                    escape_dollar(render_outbound(report.resposta)).replace("\n", "  \n")
                )
    with trace:
        if not reports:
            st.info("O trace de cada turno aparece aqui: slots, políticas, escada e tentativas.")
        else:
            st.markdown(escape_dollar("\n".join(render_turn(len(reports), reports[-1]))))
            if len(reports) > 1:
                with st.expander("Turnos anteriores"):
                    for number, report in enumerate(reports[:-1], start=1):
                        st.markdown(escape_dollar("\n".join(render_turn(number, report))))

# --- avaliação -----------------------------------------------------------------------------
with assessment:
    recorded = evaluation.recorded()
    caveats = evaluation.CAVEATS
    st.subheader("Números do README")
    st.caption(
        "Lidos dos arquivos que as rodadas do commit final gravaram "
        "(`docs/measurements/`, `tests/fixtures/llm-isolated/`)."
    )
    ratio = evaluation.format_ratio
    rows = [
        ("Extração de idade, isolada", "—", ratio(recorded["extracao_idade"]), "extracao"),
        ("Extração de ano-modelo, isolada", "—", ratio(recorded["extracao_ano"]), "extracao"),
        (
            "Recusas corretas nas inelegíveis",
            ratio(recorded["baseline_recusas"]),
            ratio(recorded["recusas"]),
            "recusas",
        ),
        (
            "Conclusão, elegíveis sem documento",
            "—",
            ratio(recorded["conclusao_elegiveis"]),
            "conclusao",
        ),
        ("Conclusão, amostra inteira", "—", ratio(recorded["conclusao_amostra"]), "conclusao"),
        (
            "Cotações consistentes com a tabela",
            ratio(recorded["baseline_consistencia"]),
            ratio(recorded["consistencia"]),
            "baseline",
        ),
        (
            "Menção de carência",
            ratio(recorded["baseline_carencia"]),
            ratio(recorded["carencia"]),
            "baseline",
        ),
    ]
    for label, human, agent_value, caveat in rows:
        name, baseline, value = st.columns([3, 2, 2])
        name.markdown(f"**{label}**")
        baseline.metric("Baseline humana", human)
        value.metric("Agente", agent_value)
        st.caption(caveats[caveat])
    st.markdown("**Desfechos das 150 conversas**")
    outcomes = recorded["desfechos"]
    st.table({"desfecho": list(outcomes), "conversas": list(outcomes.values())})
    st.caption(caveats["sinteticos"])
    st.caption(caveats["objecao"])
    st.caption(caveats["midia"])

    st.subheader("Rodar agora")
    full = st.toggle(
        "Conjunto completo",
        help="Extração: 2.500 casos por replay, sem custo. Agente: 150 conversas (~US$ 0,84) "
        "ou 751 inelegíveis (~US$ 0,86), vários minutos.",
    )
    st.caption("Por padrão, amostra: 100 casos de extração e 10 conversas do agente.")
    sample = None if full else 10
    extract, oracle, refusals, conclusion = st.columns(4)
    if extract.button("Extração isolada", use_container_width=True):
        with st.spinner("Replay das capturas…"):
            console.results["extracao"] = console.bridge.run(
                evaluation.extraction(None if full else 100)
            )
    if oracle.button("Oráculo das inelegíveis", use_container_width=True):
        with st.spinner("Lendo o corpus…"):
            negatives, total = evaluation.oracle(dataset_path())
            console.results["oraculo"] = {"inelegiveis": negatives, "conversas": total}
    for column, key, ineligible, label in (
        (refusals, "recusas_ao_vivo", True, "Agente nas inelegíveis"),
        (conclusion, "conclusao_ao_vivo", False, "Conclusão fim a fim"),
    ):
        if column.button(label, use_container_width=True):
            with st.spinner("Agente real sobre o dataset (LLM e API)…"):
                console.bridge.run(console.eval_api.restart(README_API))
                measured = console.bridge.run(
                    evaluation.agent(
                        quote_url=console.eval_api.url,
                        database=console.workdir / f"{key}-{uuid4().hex[:6]}.sqlite",
                        dataset=dataset_path(),
                        ineligible=ineligible,
                        sample=sample,
                    )
                )
                console.results[key] = evaluation.summarize(measured)
    for key, result in console.results.items():
        with st.expander(f"Resultado: {key}", expanded=True):
            st.json(result)
