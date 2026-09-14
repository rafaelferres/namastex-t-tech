"""Aba de avaliação do console: as mesmas funções de tests/ e scripts/, nunca uma cópia.

Números registrados saem dos JSON que essas funções gravaram; rodadas ao vivo chamam as
mesmas funções. Métrica que só existisse aqui não rodaria em CI.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# ponytail: tests/ e scripts/ não são pacotes instalados; o console roda da raiz do repositório.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.acceptance import AcceptanceRules  # noqa: E402
from interfaces.replay import read_rows  # noqa: E402
from scripts.measure_end_to_end import measure  # noqa: E402
from tests.golden.harness import cases_from_rows  # noqa: E402
from tests.golden.isolated import run_isolated  # noqa: E402
from tests.regression.oracle import negative_cases  # noqa: E402

__all__ = ["cases_from_rows", "measure", "negative_cases", "run_isolated"]

MEASUREMENTS = ROOT / "docs/measurements"
ISOLATED = ROOT / "tests/fixtures/llm-isolated"
ISOLATED_MODEL = "openai/gpt-4.1-mini"
PLANS = ROOT / "tests/fixtures/plans.json"
SAMPLE_SEED = 2026

type Ratio = tuple[int, int]

# As ressalvas do README, ao lado de cada número.
CAVEATS: dict[str, str] = {
    "extracao": (
        "Só o extrator, sem cotação nem orçamento de turno, por replay das capturas gravadas "
        "(sem rede nem custo). O gabarito nunca entra no contexto."
    ),
    "recusas": (
        "Idade fora de 18–75 ou veículo com mais de 20 anos na data da conversa. Recusadas pela "
        "regra local, com o motivo, sem chamar a /quote e sem escalar. A fração cresce com o "
        "tempo: a idade do veículo usa a data de hoje."
    ),
    "conclusao": (
        "Há duas taxas. 48,0% (72/150) é sobre a amostra inteira, que inclui 45 inelegíveis — "
        "recusados, como devem — e 31 elegíveis que mandaram documento e escalam por decisão de "
        "privacidade. 97,3% (72/74) é sobre as elegíveis sem documento, a taxa que mede o agente."
    ),
    "sinteticos": (
        "127 dos 673 turnos (18,9%) são sintéticos: o dataset nunca traz data de vigência. O "
        "harness responde quando o agente pergunta e, se a conversa acaba sem cotação, pede o "
        "plano Completo. Sem isso, nenhuma conversa do dataset cotaria."
    ),
    "objecao": (
        "O roteamento de objeção sobre o dataset é 100% por construção: a lista lexical foi "
        "escrita sobre as frases do gerador. O número que generaliza é o do modelo: nas 39 "
        "conversas com objeção, as 39 foram ao nó de objeção, classificadas pelo modelo."
    ),
    "midia": (
        "A resolução de mídia foi exercitada só por fixtures. O dataset traz marcadores "
        "([imagem] ...), não arquivos; no replay a resolução nunca acontece."
    ),
    "baseline": (
        "Os denominadores diferem de propósito: a baseline humana é sobre as 2.500 cotações do "
        "histórico; a do agente, sobre as cotações que ele de fato fez."
    ),
}


def format_ratio(pair: Ratio) -> str:
    """"2.499/2.500 (99,96%)": duas casas, sem o zero final, como no README."""
    done, total = pair
    if not total:
        return "—"
    percent = f"{100 * done / total:.2f}"
    percent = percent[:-1] if percent.endswith("0") else percent
    count = f"{done:,}/{total:,}".replace(",", ".")
    return f"{count} ({percent.replace('.', ',')}%)"


def _json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    """Resumo de uma rodada de `measure`, com os denominadores do README."""
    outcomes: dict[str, int] = report["desfechos"]
    total = int(report["amostra"])
    quoted = outcomes.get("cotada", 0)
    refused = outcomes.get("recusa_regra", 0)
    # Elegíveis sem documento: tira da amostra os inelegíveis e quem mandou documento.
    eligible = total - refused - outcomes.get("documento", 0)
    check = report["verificacao_cotacoes"]
    return {
        "cotadas": (quoted, total),
        "elegiveis": (quoted, eligible),
        "recusas": (refused, total),
        "precos_apresentados": int(report["apresentaram_preco"]),
        "consistencia": (check["consistentes_com_a_tabela"], check["cotacoes_verificadas"]),
        "carencia": (check["carencia_mencionada"], check["carencia_aplicavel"]),
        "sinteticos": (report["turnos_sinteticos"], report["turnos_totais"]),
        "desfechos": dict(outcomes),
        "custo_usd": report["custo_conhecido_usd"],
    }


def recorded() -> dict[str, Any]:
    """Os números do README, lidos dos arquivos que as rodadas do commit final gravaram."""
    isolated = _json(next((ISOLATED / "models").glob("openai-gpt-4.1-mini-*/report.json")))
    e2e = summarize(_json(MEASUREMENTS / "task13-e2e.json"))
    refusals = summarize(_json(MEASUREMENTS / "task13-e2e-751.json"))
    facts = _json(MEASUREMENTS / "dataset-facts.json")
    seller_ineligible = facts["vendedor_cotou_inelegivel"]
    total = int(isolated["total_cases"])
    return {
        "extracao_idade": (isolated["slots"]["idade"]["correct_all_cases"], total),
        "extracao_ano": (isolated["slots"]["veiculo_ano"]["correct_all_cases"], total),
        "recusas": refusals["recusas"],
        "precos_em_inelegiveis": refusals["precos_apresentados"],
        "conclusao_elegiveis": e2e["elegiveis"],
        "conclusao_amostra": e2e["cotadas"],
        "desfechos": e2e["desfechos"],
        "consistencia": e2e["consistencia"],
        "carencia": e2e["carencia"],
        "sinteticos": e2e["sinteticos"],
        "baseline_recusas": (
            seller_ineligible["denominador"] - seller_ineligible["valor"],
            seller_ineligible["denominador"],
        ),
        "baseline_consistencia": (
            facts["cotacao_vendedor_bate_com_quote"]["valor"],
            facts["conversas_com_cotacao_do_vendedor"]["valor"],
        ),
        "baseline_carencia": (
            facts["cotacao_menciona_carencia"]["valor"],
            facts["cotacao_menciona_carencia"]["denominador"],
        ),
    }


async def extraction(limit: int | None) -> dict[str, Any]:
    """Extração isolada por replay das capturas: o mesmo caminho de `scripts.evaluate_isolated`."""
    report: dict[str, Any] = await run_isolated(
        mode="replay", directory=ISOLATED, model=ISOLATED_MODEL, limit=limit
    )
    return report


def oracle(dataset: Path) -> Ratio:
    """Inelegíveis pelo oráculo negativo, sobre o corpus: (inelegíveis, conversas)."""
    cases = cases_from_rows(read_rows(dataset))
    return len(negative_cases(cases, _rules())), len(cases)


async def agent(
    *, quote_url: str, database: Path, dataset: Path, ineligible: bool, sample: int | None
) -> dict[str, Any]:
    """Agente real sobre o dataset, pelo harness de `scripts.measure_end_to_end`.

    `sample=None` é o conjunto completo: 150 conversas, ou as 751 inelegíveis.
    """
    conversations = None
    if ineligible and sample is not None:
        ids = sorted(
            case.conversation_id
            for case in negative_cases(cases_from_rows(read_rows(dataset)), _rules())
        )
        conversations = ",".join(random.Random(SAMPLE_SEED).sample(ids, sample))
    args = argparse.Namespace(
        scenario="p999",
        quote_url=quote_url,
        dataset=dataset,
        database=str(database),
        sample=sample or 150,
        seed=SAMPLE_SEED,
        concurrency=8,
        conversations=conversations,
        ineligible=ineligible and sample is None,
    )
    report: dict[str, Any] = await measure(args)
    return report


def _rules() -> AcceptanceRules:
    return AcceptanceRules.from_api(_json(PLANS))
