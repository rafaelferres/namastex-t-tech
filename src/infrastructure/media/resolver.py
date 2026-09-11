"""Resolução de mídia por modelo multimodal: imagem classificada, áudio transcrito.

Documento não tem adaptador: nunca sai do processo (ARQUITETURA §10). O arquivo é
lido do `media_ref` local; baixar mídia do WhatsApp fica com o futuro adapter do canal.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from application.llm import LLMAttachment, LLMClient, LLMContractError, LLMRequest, LLMRole
from domain.messages import InboundMessage, MediaResolution

_IMAGE_PROMPT = (
    "Classifique a imagem recebida numa conversa de seguro auto. e_veiculo: a imagem "
    "mostra um carro ou outro veículo? confianca: alta só se não houver dúvida. "
    "Não descreva pessoas nem leia placas ou documentos."
)
_AUDIO_PROMPT = "Transcreva literalmente o áudio em português. Não resuma nem complete."
# PNG 1x1 para a verificação de partida: mesmos parâmetros, custo mínimo.
_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class _Image(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    e_veiculo: bool
    confianca: Literal["alta", "baixa"]


class _Audio(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    transcricao: str


class LLMMediaResolver:
    def __init__(self, client: LLMClient, *, budget: float = 10.0) -> None:
        self._client = client
        self._budget = budget

    async def resolve(self, message: InboundMessage) -> MediaResolution | None:
        if message.tipo not in ("image", "audio") or not message.media_ref:
            return None
        ref = message.media_ref
        data = await asyncio.to_thread(Path(ref).read_bytes)
        if message.tipo == "image":
            mime = mimetypes.guess_type(ref)[0] or "image/jpeg"
            image = await self._ask(
                message.conversation_id, _IMAGE_PROMPT, LLMAttachment("imagem", mime, data), _Image
            )
            return MediaResolution(e_veiculo=image.e_veiculo, confianca=image.confianca)
        audio = await self._ask(
            message.conversation_id,
            _AUDIO_PROMPT,
            LLMAttachment("audio", Path(ref).suffix.lstrip(".") or "wav", data),
            _Audio,
        )
        text = audio.transcricao.strip()
        return MediaResolution(transcricao=text) if text else None

    async def probe(self) -> None:
        """Verificação de partida com os mesmos parâmetros de produção."""
        await self._ask(
            "verificacao-partida",
            _IMAGE_PROMPT,
            LLMAttachment("imagem", "image/png", _PROBE_PNG),
            _Image,
        )

    async def _ask[T: BaseModel](
        self, conversation_id: str, prompt: str, attachment: LLMAttachment, schema: type[T]
    ) -> T:
        request = LLMRequest(
            conversation_id,
            LLMRole.MEDIA,
            prompt,
            "Analise o anexo.",
            schema.model_json_schema(),
            self._budget,
            anexos=(attachment,),
        )
        response = await self._client.complete(request)
        try:
            return schema.model_validate_json(response.content)
        except ValidationError:
            raise LLMContractError(detalhe="resposta de mídia fora do schema") from None
