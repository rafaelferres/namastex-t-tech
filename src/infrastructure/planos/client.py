from __future__ import annotations

import math

import httpx

from application.ports import Clock
from domain.acceptance import AcceptanceRules
from domain.quote import QuoteContractError
from infrastructure.planos.projections import Planos, project_planos


class PlanosUnavailable(Exception):
    """O catálogo não pôde ser consultado; nenhum payload externo é exposto."""


class PlanosClient:
    def __init__(
        self, client: httpx.AsyncClient, *, clock: Clock, ttl: float, timeout: float
    ) -> None:
        if not math.isfinite(ttl) or ttl < 0:
            raise ValueError("TTL deve ser finito e não negativo")
        self._client = client
        self._clock = clock
        self._ttl = ttl
        self._timeout = timeout
        self._catalog: Planos | None = None
        self._expires_at = 0.0

    async def get(self) -> Planos:
        if self._catalog is not None and self._clock.monotonic() < self._expires_at:
            return self._catalog
        try:
            response = await self._client.get(
                "/planos", timeout=self._timeout, follow_redirects=False
            )
            response.raise_for_status()
        except httpx.HTTPError:
            raise PlanosUnavailable("Catálogo de planos indisponível") from None
        try:
            payload: object = response.json()
        except ValueError:
            raise QuoteContractError("Catálogo de planos fora do contrato") from None
        catalog = project_planos(payload)
        self._catalog = catalog
        self._expires_at = self._clock.monotonic() + self._ttl
        return catalog

    async def current(self) -> AcceptanceRules | None:
        try:
            return (await self.get()).acceptance_rules
        except PlanosUnavailable:
            return None
