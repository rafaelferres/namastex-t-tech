"""Mídia: imagem nunca escala, áudio pede texto antes de escalar, documento sempre escala."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.nodes.extract import ExtractionResult
from agent.prompts.converser import CONVERSER_PROMPT
from agent.schemas.slots import Slots
from agent.templates import (
    render_handoff,
    render_media_note,
    render_objection,
    render_safe_reply,
    render_unavailable,
)
from application.ingest import IngestedTurn, Ingestor
from domain.handoff import ConversationContext, HandoffPolicy, HandoffReason
from domain.messages import InboundMessage, Intent, MediaResolution, OutboundMessage, PedirDado
from domain.objection import Objecao
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.media.resolver import LLMMediaResolver
from infrastructure.privacy import PrivacyRedactor
from interfaces.rendering import render_outbound
from tests.fakes import FakeClock
from tests.unit.test_graph import FACTS, build
from tests.unit.test_ingest import MemoryStore
from tests.virtual_time import virtual_time

MEDIA = Path(__file__).parents[1] / "fixtures" / "media"
# Pedido de envio de documento, foto ou CPF; "mandar por escrito" não conta.
ASKS_FOR_FILE = re.compile(
    r"\b(?:envi|mand|anex|encaminh)\w*\b.{0,30}\b(?:documento|foto|imagem|cpf|cnh|crlv)",
    re.IGNORECASE,
)
IMAGE_CASES = {
    "veiculo_alta": MediaResolution(e_veiculo=True, confianca="alta"),
    "veiculo_baixa": MediaResolution(e_veiculo=True, confianca="baixa"),
    "nao_veiculo": MediaResolution(e_veiculo=False, confianca="alta"),
    "sem_resolucao": None,
}


def pending_slots() -> Slots:
    """Falta a data de vigência: toda resposta de coleta tem uma pergunta pendente."""
    return Slots.model_validate(
        {
            key: {"valor": value, "status": "informado", "proveniencia": "digitado"}
            for key, value in {"idade": 30, "veiculo_ano": 2020, "plano_id": "completo"}.items()
        }
    )


def message(kind, index, resolution=None, body=None, ref=None):
    marker = {"image": "[imagem] foto.jpg", "audio": "[audio] audio.ogg"}
    return InboundMessage(
        "replay",
        "c",
        "lead",
        kind,
        body or marker.get(kind, "[documento] CNH_frente.pdf"),
        f"m{index}",
        index,
        ref,
        resolucao=resolution,
    )


def respond(graph, clock, *messages):
    return clock.run(graph.respond(IngestedTurn("c", tuple(messages), 0)))


def test_policy_never_escalates_image_escalates_nth_audio_and_every_document():
    policy = HandoffPolicy()
    image = ConversationContext(tipo_midia="imagem", midia_resolvida=False)
    assert not policy.evaluate(image).escalar
    assert not policy.evaluate(ConversationContext(audios_nao_resolvidos=1)).escalar
    audios = ConversationContext(audios_nao_resolvidos=2)
    assert policy.evaluate(audios).motivo is HandoffReason.MIDIA
    document = ConversationContext(tipo_midia="documento")
    assert policy.evaluate(document).motivo is HandoffReason.DOCUMENTO


@pytest.mark.parametrize("case", sorted(IMAGE_CASES))
def test_image_never_escalates_and_keeps_asking_in_text(plans_payload, quote_payload, case):
    with virtual_time() as clock:
        graph, extractor, _, _, handoff = build(clock, plans_payload, quote_payload)
        extractor.extract.return_value = ExtractionResult(pending_slots())
        reply = respond(graph, clock, message("image", 0, IMAGE_CASES[case]))
    assert reply.intent is Intent.PEDIR_DADO
    handoff.assert_not_called()
    text = render_outbound(reply)
    assert "data desejada para início da vigência" in text
    assert not re.search(r"não (?:é|parece)", text, re.IGNORECASE)  # nunca acusa o lead
    # Confiança baixa é "não sei", nunca "não é carro".
    assert reply.payload.nota == ("foto_veiculo" if case == "veiculo_alta" else "foto_neutra")


def test_unresolved_audio_asks_text_first_and_escalates_on_the_second(plans_payload, quote_payload):
    with virtual_time() as clock:
        graph, extractor, _, _, handoff = build(clock, plans_payload, quote_payload)
        extractor.extract.return_value = ExtractionResult(pending_slots())
        first = respond(graph, clock, message("audio", 0))
        second = respond(graph, clock, message("audio", 1))
    assert first.intent is Intent.PEDIR_DADO
    assert first.payload == PedirDado("data_inicio", nota="audio_sem_texto")
    assert "por escrito" in render_outbound(first)
    assert second.intent is Intent.ESCALAR
    assert second.payload.motivo is HandoffReason.MIDIA
    assert handoff.await_count == 1


def test_transcribed_audio_is_extracted_with_transcribed_provenance(plans_payload, quote_payload):
    with virtual_time() as clock:
        graph, extractor, _, _, handoff = build(clock, plans_payload, quote_payload)
        extractor.extract.return_value = ExtractionResult(pending_slots())
        transcribed = message("audio", 0, MediaResolution(), body="tenho trinta e dois anos")
        respond(graph, clock, transcribed)
    call = extractor.extract.call_args
    assert "tenho trinta e dois anos" in call.args[0]
    assert call.kwargs["proveniencia"] == "transcrito"
    handoff.assert_not_called()


@pytest.mark.parametrize("first", [None, "image", "audio"])
def test_document_always_escalates(plans_payload, quote_payload, first):
    with virtual_time() as clock:
        graph, extractor, _, _, handoff = build(clock, plans_payload, quote_payload)
        extractor.extract.return_value = ExtractionResult(pending_slots())
        messages = [message(first, 0)] if first else []
        reply = respond(graph, clock, *messages, message("document", 1))
    assert reply.intent is Intent.ESCALAR
    assert reply.payload.motivo is HandoffReason.DOCUMENTO


@pytest.mark.asyncio
async def test_ingestion_resolves_media_without_blocking_and_never_sends_documents():
    clock = FakeClock()
    store, turns = MemoryStore(), []

    async def consume(turn):
        turns.append(turn)

    async def sleep(_):
        clock.elapsed += 1

    resolver = AsyncMock()
    resolver.resolve.side_effect = [
        MediaResolution(transcricao="meu cpf é 529.982.247-25"),
        RuntimeError("classificador fora do ar"),
    ]
    async with Ingestor(
        store, store, store, PrivacyRedactor(), consume, clock=clock, sleep=sleep, media=resolver
    ) as ingest:
        for index, (kind, ref) in enumerate(
            [("audio", "a.wav"), ("image", "f.jpg"), ("document", "cnh.pdf")]
        ):
            await ingest.ingest(message(kind, index, ref=ref))
    assert [call.args[0].tipo for call in resolver.resolve.call_args_list] == ["audio", "image"]
    audio, image, document = sorted(
        (item for turn in turns for item in turn.messages), key=lambda item: item.indice
    )
    assert audio.corpo == "meu cpf é [CPF]" and audio.media_status == "resolvido"
    assert image.resolucao is None and image.media_status == "nao_resolvido"
    assert document.media_status == "nao_resolvido"


def media_client(captured, content):
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "m",
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                "choices": [{"message": {"content": json.dumps(content)}}],
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def media_resolver(http):
    return LLMMediaResolver(OpenRouterLLMClient(http, LLMConfig(api_key="k"), FakeClock()))


@pytest.mark.asyncio
async def test_adapter_sends_image_as_data_uri_and_parses_classification():
    captured = []
    path = MEDIA / "veiculo_nitido.jpg"
    async with media_client(captured, {"e_veiculo": True, "confianca": "alta"}) as http:
        resolution = await media_resolver(http).resolve(message("image", 0, ref=str(path)))
    assert resolution == MediaResolution(e_veiculo=True, confianca="alta")
    content = captured[0]["messages"][-1]["content"]
    encoded = base64.b64encode(path.read_bytes()).decode()
    assert {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}} in (
        content
    )


@pytest.mark.asyncio
async def test_adapter_sends_audio_as_input_audio_and_returns_transcript():
    captured = []
    path = MEDIA / "audio_curto.wav"
    async with media_client(captured, {"transcricao": "tenho trinta e dois anos"}) as http:
        resolution = await media_resolver(http).resolve(message("audio", 0, ref=str(path)))
    assert resolution == MediaResolution(transcricao="tenho trinta e dois anos")
    part = captured[0]["messages"][-1]["content"][-1]
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "wav"


@pytest.mark.asyncio
async def test_adapter_never_sends_documents():
    captured = []
    async with media_client(captured, {}) as http:
        document = message("document", 0, ref="cnh.pdf")
        assert await media_resolver(http).resolve(document) is None
    assert captured == []


def test_prompt_asks_only_the_five_fields_in_text():
    prompt = CONVERSER_PROMPT.casefold()
    for field in ("plano", "idade", "ano-modelo", "cep", "data de início"):
        assert field in prompt
    assert "nunca peça documento, foto nem cpf" in prompt
    assert not ASKS_FOR_FILE.search(CONVERSER_PROMPT)


def test_no_fixed_text_asks_for_documents_or_photos():
    questions = [
        render_outbound(OutboundMessage("c", Intent.PEDIR_DADO, PedirDado(slot)))
        for slot in ("idade", "veiculo_ano", "cep", "data_inicio")
    ]
    notes = [render_media_note(note) for note in ("foto_veiculo", "foto_neutra", "audio_sem_texto")]
    texts = [
        render_handoff(),
        render_unavailable(),
        render_safe_reply(FACTS),
        *notes,
        *(render_objection(item) for item in Objecao),
        *questions,
    ]
    for text in texts:
        assert not ASKS_FOR_FILE.search(text), text
