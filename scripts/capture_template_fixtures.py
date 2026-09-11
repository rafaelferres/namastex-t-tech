"""Executa o cotar original do desafio offline; não replica sua fórmula."""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
from datetime import date
from pathlib import Path
from types import SimpleNamespace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("tests/fixtures/presentation"))
    args = parser.parse_args()
    namespace = runpy.run_path(str(args.api_source))

    class ReferenceDate(date):
        @classmethod
        def today(cls) -> ReferenceDate:
            return cls(2026, 9, 11)

    quote = namespace["cotar"]
    quote.__globals__["dt"] = SimpleNamespace(date=ReferenceDate)
    args.output.mkdir(parents=True, exist_ok=True)
    for plan in ("essencial", "completo", "premium"):
        for region, cep in (("normal", "01310100"), ("agravado", "07000000")):
            for period, start in (("integral", "2026-09-01"), ("prorata", "2026-09-15")):
                request = {
                    "plano_id": plan,
                    "idade": 30,
                    "veiculo_ano": 2026,
                    "cep": cep,
                    "data_inicio": start,
                }
                response = quote(request)
                key = f"{plan}-{region}-{period}"
                (args.output / f"{key}.json").write_text(json.dumps(response, indent=2) + "\n")
                print(key, response["premio_mensal"], response.get("primeiro_pagamento_pro_rata"))
    (args.output / "PROVENANCE.md").write_text(
        "# Origem dos fixtures\n\n"
        "Saídas do cotar original do desafio, sem rede, com data de referência\n"
        "2026-09-11. Perfil sintético: 30 anos, veículo 2026; CEPs 01310100 e\n"
        "07000000; início 2026-09-01 ou 2026-09-15. Fórmula não reimplementada.\n\n"
        f"SHA-256 de quote_logic.py: {hashlib.sha256(args.api_source.read_bytes()).hexdigest()}\n"
    )


if __name__ == "__main__":
    main()
