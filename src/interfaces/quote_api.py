"""API de cotação do desafio como processo filho do console (tarefa 14).

A taxa de falha e a semente são variáveis de ambiente lidas na partida da API; para mudá-las
ao vivo, o console reinicia o processo. Ferramenta de demonstração, não caminho de produção.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True, slots=True)
class QuoteApiSettings:
    failure_rate: float = 0.20
    slow_rate: float = 0.10
    seed: int | None = 42


def api_environment(settings: QuoteApiSettings) -> dict[str, str]:
    env = {
        "QUOTE_FAILURE_RATE": f"{settings.failure_rate:g}",
        # Com falha em 1,0 o sorteio inteiro já falha; lentidão só cabe no que sobra.
        "QUOTE_SLOW_RATE": f"{min(settings.slow_rate, 1 - settings.failure_rate):g}",
    }
    if settings.seed is not None:
        env["QUOTE_SEED"] = str(settings.seed)
    return env


class ManagedQuoteApi:
    def __init__(
        self,
        service_dir: Path,
        port: int,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._service_dir = service_dir
        self._port = port
        self._sleep = sleep
        self._process: asyncio.subprocess.Process | None = None
        self.settings: QuoteApiSettings | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def restart(self, settings: QuoteApiSettings) -> None:
        """Instância nova: o sorteio da semente recomeça na primeira /quote."""
        await self.stop()
        self._process = await asyncio.create_subprocess_exec(
            "uv", "run", "--with", "fastapi", "--with", "uvicorn",
            "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self._port),
            cwd=self._service_dir,
            env={**os.environ, **api_environment(settings)},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,  # `uv run` tem filho: o grupo inteiro sai junto
        )
        self.settings = settings
        await self._wait_healthy()

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        await process.wait()

    async def _wait_healthy(self, attempts: int = 240) -> None:
        async with httpx.AsyncClient(base_url=self.url, timeout=1.0) as client:
            for _ in range(attempts):
                if self._process is None or self._process.returncode is not None:
                    raise RuntimeError(f"API de cotação não subiu em {self._service_dir}")
                try:
                    if (await client.get("/health")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await self._sleep(0.25)
        raise RuntimeError("API de cotação não respondeu /health em 60 s")
