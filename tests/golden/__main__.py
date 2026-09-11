"""Executa a mesma métrica na amostra redigida ou no corpus local completo."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from domain.acceptance import AcceptanceRules
from interfaces.replay import dataset_path, read_rows
from tests.golden.harness import Case, Extractor, NullExtractor, cases_from_rows, evaluate
from tests.regression.oracle import negative_cases


async def run(extractor: Extractor, *, full: bool = False) -> str:
    fixtures = Path(__file__).parents[1] / "fixtures"
    if full:
        cases = cases_from_rows(read_rows(dataset_path()))
    else:
        rows = json.loads((fixtures / "evaluation/sample.json").read_text())
        cases = tuple(Case(**{**row, "messages": tuple(row["messages"])}) for row in rows)
    rules = AcceptanceRules.from_api(json.loads((fixtures / "plans.json").read_text()))
    report = await evaluate(cases, extractor)
    return (
        f"Conversas: {report.total}\n"
        f"Inelegíveis: {len(negative_cases(cases, rules))}\n"
        f"Acurácia idade: {report.idade_accuracy:.2%}\n"
        f"Acurácia veículo: {report.veiculo_accuracy:.2%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if args.full and not dataset_path().is_file():
        parser.error("Corpus local ausente; configure AUTOSEGURO_DATASET")
    print(asyncio.run(run(NullExtractor(), full=args.full)))


if __name__ == "__main__":
    main()
