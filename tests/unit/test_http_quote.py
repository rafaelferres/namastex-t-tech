from __future__ import annotations

import asyncio
import json
import traceback
from datetime import date
from typing import Any

import httpx
import pytest

from application.ports import QuoteProvider
from domain.quote import Declined, Quote, QuoteContractError, QuoteRequest, QuoteUnavailable
from infrastructure.quote.http import HttpQuoteProvider
from tests.fakes import FakeClock


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 201, 204, 301, 302, 401, 404, 418])
async def test_contract_status_is_not_transient_and_is_called_once(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="01310-100", headers={"location": "/redirect"})

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        provider: QuoteProvider = HttpQuoteProvider(client, timeout=2.0, clock=FakeClock())
        with pytest.raises(QuoteContractError) as caught:
            await provider.quote(QuoteRequest("completo", 30, 2027))
    assert calls == 1
    assert caught.value.ano_normalizado is True
    assert "01310-100" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 501, 502, 503, 504, 599, 408, 425, 429])
async def test_transient_status_is_unavailable_once(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "upstream_unavailable"})

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        provider = HttpQuoteProvider(client, timeout=2.0, clock=FakeClock())
        with pytest.raises(QuoteUnavailable) as caught:
            await provider.quote(QuoteRequest("completo", 30, 2027))
    assert calls == 1
    assert caught.value.suspeita_contrato is False
    assert caught.value.ano_normalizado is True


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"server error", b"{}", b"[]", b"null", b'{"error":"bug"}'])
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_unexpected_server_error_is_still_transient_but_suspicious(
    status: int, body: bytes
) -> None:
    async with httpx.AsyncClient(
        base_url="https://quote.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(status, content=body)),
    ) as client:
        with pytest.raises(QuoteUnavailable) as caught:
            await HttpQuoteProvider(client, timeout=2.0, clock=FakeClock()).quote(
                QuoteRequest("completo", 30, 2026)
            )
    assert caught.value.suspeita_contrato is True
    assert caught.value.ano_normalizado is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.RemoteProtocolError],
)
async def test_transport_errors_are_unavailable_without_leaking_original_error(
    error_type: type[httpx.TransportError],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise error_type("Falhou para CEP 01310100", request=request)

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(QuoteUnavailable) as caught:
            await HttpQuoteProvider(client, timeout=2.0, clock=FakeClock()).quote(
                QuoteRequest("completo", 30, 2027, "01310-100")
            )
    assert calls == 1
    assert caught.value.suspeita_contrato is False
    assert caught.value.ano_normalizado is True
    assert "01310100" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_decline_returns_the_insurers_reason() -> None:
    async with httpx.AsyncClient(
        base_url="https://quote.test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                422, json={"error": "cotacao_recusada", "motivo": "Perfil recusado pela seguradora"}
            )
        ),
    ) as client:
        outcome = await HttpQuoteProvider(client, timeout=2.0, clock=FakeClock()).quote(
            QuoteRequest("completo", 76, 2027)
        )
    assert isinstance(outcome, Declined)
    assert outcome.motivo == "Perfil recusado pela seguradora"
    assert outcome.ano_normalizado is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [b"not JSON", b"{}", b"null", b"[]", b'{"motivo":null}', b'{"motivo":123}', b'{"motivo":" "}'],
)
async def test_malformed_decline_uses_generic_reason(body: bytes) -> None:
    async with httpx.AsyncClient(
        base_url="https://quote.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(422, content=body)),
    ) as client:
        outcome = await HttpQuoteProvider(client, timeout=2.0, clock=FakeClock()).quote(
            QuoteRequest("completo", 30, 2026)
        )
    assert isinstance(outcome, Declined)
    assert outcome.motivo == "Cotação recusada pela seguradora."


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not JSON", b"{}", b"null", b"[]"])
async def test_malformed_success_is_contract_error(body: bytes) -> None:
    async with httpx.AsyncClient(
        base_url="https://quote.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    ) as client:
        with pytest.raises(QuoteContractError) as caught:
            await HttpQuoteProvider(client, timeout=2.0, clock=FakeClock()).quote(
                QuoteRequest("completo", 30, 2027)
            )
    assert caught.value.ano_normalizado is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("year", "sent", "normalized"),
    [
        (2027, 2026, True),
        (2028, 2028, False),
        (2026, 2026, False),
        (2005, 2005, False),
    ],
)
@pytest.mark.parametrize("cep", ["01310-100", None])
async def test_wire_payload_preserves_slots_and_normalizes_only_next_year(
    quote_payload: dict[str, Any], year: int, sent: int, normalized: bool, cep: str | None
) -> None:
    req = QuoteRequest("completo", 30, year, cep, date(2026, 9, 15))
    original_fingerprint = req.fingerprint(date(2026, 9, 11))
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/quote"
        assert request.extensions["timeout"] == dict(connect=1.25, read=1.25, write=1.25, pool=1.25)
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=quote_payload)

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await HttpQuoteProvider(client, timeout=1.25, clock=FakeClock()).quote(req)
        assert not client.is_closed
    expected: dict[str, str | int] = {
        "plano_id": "completo",
        "idade": 30,
        "veiculo_ano": sent,
        "data_inicio": "2026-09-15",
    }
    if cep is not None:
        expected["cep"] = "01310100"
    assert captured == [expected]
    assert isinstance(result, Quote)
    assert result.premio_mensal == Quote.from_api(quote_payload).premio_mensal
    assert result.ano_normalizado is normalized
    assert req.veiculo_ano == year
    assert req.fingerprint(date(2026, 9, 11)) == original_fingerprint


@pytest.mark.asyncio
async def test_metadata_is_per_call_even_with_overlapping_requests(
    quote_payload: dict[str, Any],
) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
        else:
            release_first.set()
        return httpx.Response(200, json=quote_payload)

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        provider = HttpQuoteProvider(client, timeout=2.0, clock=FakeClock())
        async with asyncio.TaskGroup() as group:
            first = group.create_task(provider.quote(QuoteRequest("completo", 30, 2027)))
            await first_started.wait()
            second = group.create_task(provider.quote(QuoteRequest("completo", 30, 2020)))
    assert first.result().ano_normalizado is True
    assert second.result().ano_normalizado is False


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_unavailability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await HttpQuoteProvider(client, timeout=2.0, clock=FakeClock()).quote(
                QuoteRequest("completo", 30, 2027)
            )


@pytest.mark.asyncio
async def test_corrupted_compression_is_unavailable_with_normalization_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"not gzip", headers={"content-encoding": "gzip"})

    async with httpx.AsyncClient(
        base_url="https://quote.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(QuoteUnavailable) as caught:
            await HttpQuoteProvider(client, timeout=2.0, clock=FakeClock()).quote(
                QuoteRequest("completo", 30, 2027)
            )
    assert caught.value.ano_normalizado is True
