from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import httpx

from application.ports import Clock
from domain.quote import (
    Declined,
    Quote,
    QuoteConfigurationError,
    QuoteContractError,
    QuoteOutcome,
    QuoteRequest,
    QuoteUnavailable,
)
from infrastructure.http_errors import describe, describe_transport, is_transient

logger = logging.getLogger(__name__)
# 400 é payload nosso (contrato) e 422 é recusa; rota e credencial são configuração.
_CONFIGURATION = frozenset({401, 403, 404, 405})


def _decode(response: httpx.Response) -> object:
    try:
        return response.json(parse_float=Decimal)
    except ValueError:
        return None


class HttpQuoteProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        timeout: float,
        clock: Clock,
        *,
        observe_status: Callable[[int], None] | None = None,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._clock = clock
        self._observe_status = observe_status

    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        payload = req.to_payload()
        ano_atual = self._clock.today().year
        ano_normalizado = req.veiculo_ano == ano_atual + 1
        if ano_normalizado:
            payload["veiculo_ano"] = ano_atual
        try:
            response = await self._client.post(
                "/quote", json=payload, timeout=self._timeout, follow_redirects=False
            )
        except httpx.RequestError as error:
            detail = describe_transport(error)
            logger.warning("quote_http_unavailable %s", detail)
            raise QuoteUnavailable(ano_normalizado=ano_normalizado, detalhe=detail) from None

        status = response.status_code
        if self._observe_status is not None:
            try:
                self._observe_status(status)
            except Exception:
                logging.getLogger(__name__).warning("http_observation_failed")
        body = _decode(response)
        if status == 200:
            try:
                quote = Quote.from_api(body)
            except QuoteContractError:
                detail = describe(response)
                logger.error("quote_http_contract %s", detail)
                raise QuoteContractError(
                    "Resposta de cotação fora do contrato",
                    ano_normalizado=ano_normalizado,
                    detalhe=detail,
                ) from None
            return replace(quote, ano_normalizado=ano_normalizado)
        if status == 422:
            motivo = body.get("motivo") if isinstance(body, dict) else None
            if not isinstance(motivo, str) or not motivo.strip():
                motivo = "Cotação recusada pela seguradora."
            return Declined(motivo, ano_normalizado=ano_normalizado)
        detail = describe(response)
        if is_transient(status):
            known_failure = isinstance(body, dict) and body.get("error") == "upstream_unavailable"
            logger.warning("quote_http_unavailable %s", detail)
            raise QuoteUnavailable(
                suspeita_contrato=500 <= status < 600 and not known_failure,
                ano_normalizado=ano_normalizado,
                detalhe=detail,
            )
        if status in _CONFIGURATION or 300 <= status < 400:
            logger.error("quote_http_configuration %s", detail)
            raise QuoteConfigurationError(
                "API de cotação rejeitou a configuração",
                ano_normalizado=ano_normalizado,
                detalhe=detail,
            )
        logger.error("quote_http_contract %s", detail)
        raise QuoteContractError(ano_normalizado=ano_normalizado, detalhe=detail)
