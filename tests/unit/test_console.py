"""Console Streamlit (tarefa 14): quarto adapter, sem lógica própria.

O app não é importado aqui — o grupo `console` é opcional. As regras são verificadas no
código-fonte, e o que o app desenha sai de funções testadas sem Streamlit.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from domain.messages import InboundMessage
from interfaces import evaluation
from interfaces.conversation_report import render_turn
from tests.unit.test_cli import slots, stack_for

ROOT = Path(__file__).parents[2]
APP = ROOT / "src/interfaces/streamlit_app.py"


def _tree() -> ast.Module:
    return ast.parse(APP.read_text(encoding="utf-8"))


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _called_names() -> list[str]:
    names: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


def test_app_imports_use_cases_and_composition_only() -> None:
    forbidden = (
        "agent",
        "langgraph",
        "httpx",
        "sqlite3",
        "asyncio",
        "infrastructure.quote",
        "infrastructure.persistence",
        "infrastructure.planos",
        "infrastructure.llm",
        "domain.handoff",
        "domain.acceptance",
    )
    offending = {
        module
        for module in _imported_modules()
        if any(module == name or module.startswith(name + ".") for name in forbidden)
    }
    assert offending == set()


def test_async_bridge_is_the_only_way_into_the_event_loop() -> None:
    calls = _called_names()
    assert calls.count("AsyncBridge") == 1
    for primitive in ("new_event_loop", "run_coroutine_threadsafe", "run_until_complete"):
        assert primitive not in calls
    assert "asyncio.run(" not in APP.read_text(encoding="utf-8")


def test_session_state_keeps_only_the_thread_id() -> None:
    keys: set[object] = set()
    for node in ast.walk(_tree()):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "session_state"
        ):
            keys.add(node.attr)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "session_state"
            and isinstance(node.slice, ast.Constant)
        ):
            keys.add(node.slice.value)
    assert keys == {"thread_id"}


def test_trace_panel_is_the_same_redacted_report_as_the_cli() -> None:
    assert "render_turn" in _called_names()


@pytest.mark.asyncio
async def test_trace_panel_renders_redacted_text(
    tmp_path: Path, plans_payload: dict[str, Any], quote_payload: dict[str, Any]
) -> None:
    async with stack_for(
        tmp_path / "db.sqlite", plans_payload, quote_payload, extracted=[slots()], spoken=[]
    ) as (stack, _):
        text = "Meu CPF é 529.982.247-25, cep 01310-100, email ana@example.com"
        await stack.ingestor.ingest(InboundMessage("console", "c1", "c1", "text", text, "c1:0", 0))
        await stack.ingestor.wait_idle()
        reports = await stack.inspector.execute("c1")
    panel = "\n".join(render_turn(1, reports[-1]))
    for secret in ("529.982.247-25", "01310-100", "ana@example.com"):
        assert secret not in panel
    assert "[CPF]" in panel


def test_currency_is_not_read_as_latex_math_on_screen() -> None:
    # Streamlit lê `$…$` como LaTeX: "R$ 241,38 … R$ 3.000,00" virava fórmula na tela.
    from interfaces.rendering import escape_dollar

    text = "Mensalidade: R$ 241,38.\nFranquia: R$ 3.000,00."
    assert escape_dollar(text) == "Mensalidade: R\\$ 241,38.\nFranquia: R\\$ 3.000,00."
    assert _called_names().count("escape_dollar") >= 3  # conversa, trace e turnos anteriores


def test_evaluation_calls_the_same_functions_as_the_tests() -> None:
    from scripts.measure_end_to_end import measure
    from tests.golden.harness import cases_from_rows
    from tests.golden.isolated import run_isolated
    from tests.regression.oracle import negative_cases

    assert evaluation.run_isolated is run_isolated
    assert evaluation.measure is measure
    assert evaluation.negative_cases is negative_cases
    assert evaluation.cases_from_rows is cases_from_rows
    source = APP.read_text(encoding="utf-8")
    for reimplemented in ("negative_cases(", "measure(", "run_isolated("):
        assert reimplemented not in source


@pytest.mark.parametrize(
    ("pair", "shown"),
    [
        ((2499, 2500), "2.499/2.500 (99,96%)"),
        ((72, 74), "72/74 (97,3%)"),
        ((72, 150), "72/150 (48,0%)"),
        ((751, 751), "751/751 (100,0%)"),
        ((0, 0), "—"),
    ],
)
def test_ratio_is_shown_with_the_readme_precision(pair: tuple[int, int], shown: str) -> None:
    # Arredondar 2.499/2.500 para "100,0%" contradizia o README na própria tela.
    assert evaluation.format_ratio(pair) == shown


def test_recorded_results_are_the_numbers_in_the_readme() -> None:
    recorded = evaluation.recorded()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert recorded["extracao_idade"] == (2499, 2500)
    assert recorded["extracao_ano"] == (2500, 2500)
    assert recorded["recusas"] == (751, 751)
    assert recorded["conclusao_elegiveis"] == (72, 74)
    assert recorded["conclusao_amostra"] == (72, 150)
    assert recorded["consistencia"] == (72, 72)
    assert recorded["carencia"] == (72, 72)
    assert recorded["baseline_recusas"] == (0, 751)
    assert recorded["baseline_consistencia"] == (0, 2500)
    for text in ("**99,96%** (2.499/2.500)", "**97,3%** (72/74)", "**751 / 751**", "**72 / 72**"):
        assert text in readme
    # Número sem ressalva na tela vira número sem ressalva na cabeça de quem vê.
    assert set(evaluation.CAVEATS) >= {"conclusao", "sinteticos", "objecao", "midia"}
    assert "18,9%" in evaluation.CAVEATS["sinteticos"]
