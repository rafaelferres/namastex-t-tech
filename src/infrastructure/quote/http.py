from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import httpx

from application.ports import Clock
from domain.quote import (
    Declined,
    Quote,
    QuoteContractError,
    QuoteOutcome,
    QuoteRequest,
    QuoteUnavailable,
)


def _decode(response: httpx.Response) -> object:
    try:
        return response.json(parse_float=Decimal)
    except ValueError:
        return None


class HttpQuoteProvider:
    def __init__(self, client: httpx.AsyncClient, timeout: float, clock: Clock) -> None:
        self._client = client
        self._timeout = timeout
        self._clock = clock

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
        except httpx.RequestError:
            raise QuoteUnavailable(ano_normalizado=ano_normalizado) from None

        status = response.status_code
        body = _decode(response)
        if status == 200:
            try:
                quote = Quote.from_api(body)
            except QuoteContractError:
                raise QuoteContractError(
                    "Resposta de cotação fora do contrato", ano_normalizado=ano_normalizado
                ) from None
            return replace(quote, ano_normalizado=ano_normalizado)
        if status == 422:
            motivo = body.get("motivo") if isinstance(body, dict) else None
            if not isinstance(motivo, str) or not motivo.strip():
                motivo = "Cotação recusada pela seguradora."
            return Declined(motivo, ano_normalizado=ano_normalizado)
        if 500 <= status < 600 or status in (408, 425, 429):
            known_failure = isinstance(body, dict) and body.get("error") == "upstream_unavailable"
            raise QuoteUnavailable(
                suspeita_contrato=500 <= status < 600 and not known_failure,
                ano_normalizado=ano_normalizado,
            )
        raise QuoteContractError(ano_normalizado=ano_normalizado)
