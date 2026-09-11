from __future__ import annotations

import logging
import math

import httpx

from application.external import ConfigurationError
from application.ports import Clock
from domain.acceptance import AcceptanceRules
from domain.quote import QuoteContractError
from infrastructure.http_errors import (
    describe,
    describe_transport,
    is_configuration,
    is_transient,
)
from infrastructure.planos.projections import Planos, project_planos

logger = logging.getLogger(__name__)


class PlanosUnavailable(Exception):
    """Falha passageira do catálogo; `detalhe` traz status e corpo já redigidos."""

    def __init__(self, detalhe: str | None = None) -> None:
        super().__init__("Catálogo de planos indisponível")
        self.detalhe = detalhe


class PlanosConfigurationError(ConfigurationError):
    """Rota ou credencial rejeitada: não cai no fail-open do guard."""


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
        except httpx.RequestError as error:
            detail = describe_transport(error)
            logger.warning("planos_unavailable %s", detail)
            raise PlanosUnavailable(detail) from None
        status = response.status_code
        if status != 200:
            detail = describe(response)
            if is_transient(status):
                logger.warning("planos_unavailable %s", detail)
                raise PlanosUnavailable(detail)
            if is_configuration(status):
                logger.error("planos_configuration %s", detail)
                raise PlanosConfigurationError("planos", detail)
            logger.error("planos_contract %s", detail)
            raise QuoteContractError("Catálogo de planos fora do contrato", detalhe=detail)
        try:
            payload: object = response.json()
        except ValueError:
            detail = describe(response)
            logger.error("planos_contract %s", detail)
            raise QuoteContractError(
                "Catálogo de planos fora do contrato", detalhe=detail
            ) from None
        catalog = project_planos(payload)
        self._catalog = catalog
        self._expires_at = self._clock.monotonic() + self._ttl
        return catalog

    async def current(self) -> AcceptanceRules | None:
        try:
            return (await self.get()).acceptance_rules
        except PlanosUnavailable:
            return None
