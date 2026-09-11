"""Resolve as fixtures de mídia com o adaptador real e registra o que foi exercitado.

O dataset só traz marcadores de mídia, sem arquivo: imagem e áudio de verdade só passam
por aqui. Documento não tem adaptador e não aparece nesta sonda.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from application.ports import SystemClock
from domain.messages import InboundMessage
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.media.resolver import LLMMediaResolver

FIXTURES = {
    "veiculo_nitido.jpg": "image",
    "veiculo_ruim.jpg": "image",
    "nao_veiculo.jpg": "image",
    "audio_curto.wav": "audio",
}


async def probe(directory: Path) -> dict[str, object]:
    config = LLMConfig.from_env()
    results = []
    async with httpx.AsyncClient() as http:
        resolver = LLMMediaResolver(OpenRouterLLMClient(http, config, SystemClock()))
        for name, kind in FIXTURES.items():
            message = InboundMessage(
                "replay", "sonda-midia", "sonda", kind, f"[{kind}]", name, 0, str(directory / name)
            )  # type: ignore[arg-type]
            start = time.monotonic()
            outcome: dict[str, object]
            try:
                resolution = await resolver.resolve(message)
                outcome = {
                    "resolvido": resolution is not None,
                    "e_veiculo": resolution.e_veiculo if resolution else None,
                    "confianca": resolution.confianca if resolution else None,
                    "transcricao": resolution.transcricao if resolution else None,
                }
            except Exception as error:
                outcome = {"erro": type(error).__name__, "detalhe": getattr(error, "detalhe", None)}
            results.append(
                {
                    "fixture": name,
                    "tipo": kind,
                    "modelo": config.media_model,
                    "latencia_ms": round((time.monotonic() - start) * 1000),
                    **outcome,
                }
            )
    return {"resultados": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("tests/fixtures/media"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(asyncio.run(probe(args.directory)), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
