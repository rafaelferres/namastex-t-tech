"""Ingestão antes do agente: redação, dedup persistido e rajadas por conversa."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from types import TracebackType
from typing import Protocol, Self

from application.conversation_ports import (
    ConversationReader,
    ConversationWriter,
    MessageReader,
    MessageWriter,
)
from application.external import ConfigurationError
from application.media import MediaResolver
from application.ports import Clock
from domain.messages import InboundMessage, MediaResolution

logger = logging.getLogger(__name__)


class MessagePrivacy(Protocol):
    def redact(self, text: str) -> str: ...

    def cpf_hash(self, text: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class IngestedTurn:
    conversation_id: str
    messages: tuple[InboundMessage, ...]
    objecoes_preco: int
    private_cep: str | None = field(default=None, repr=False)


def price_objection(text: str) -> bool:
    """Piso lexical conservador; uma ocorrência por turno, não por fragmento."""
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    normalized = re.sub(r"\bnao\s+(?:(?:esta|e|achei|ficou|ta)\s+)?car[oa]\b", "", normalized)
    return bool(
        re.search(
            r"\b(?:car[oa]|(?:preco|valor)\s+(?:muito\s+)?alto|fora\s+do\s+orcamento)\b",
            normalized,
        )
    )


class Ingestor:
    def __init__(
        self,
        conversations: ConversationWriter,
        messages: MessageWriter,
        reader: ConversationReader,
        privacy: MessagePrivacy,
        consume: Callable[[IngestedTurn], Awaitable[None]],
        *,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]],
        window: float = 0.5,
        capture_cep: Callable[[str], str | None] | None = None,
        media: MediaResolver | None = None,
        media_timeout: float = 10.0,
        history: MessageReader | None = None,
    ) -> None:
        if not 0 < window < float("inf"):
            raise ValueError("Janela deve ser finita e positiva")
        self._history = history
        self._media = media
        self._media_timeout = media_timeout
        self._conversations = conversations
        self._messages = messages
        self._reader = reader
        self._privacy = privacy
        self._consume = consume
        self._clock = clock
        self._sleep = sleep
        self._window = window
        self._capture_cep = capture_cep
        self._private: dict[str, dict[str, str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, list[InboundMessage]] = {}
        self._deadlines: dict[str, float] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._ready: dict[str, IngestedTurn] = {}
        self._closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._closed = True
        await self.wait_idle()

    async def next_index(self, conversation_id: str) -> int:
        """Canal que retoma uma conversa continua a numeração já persistida."""
        if self._history is None:
            raise RuntimeError("Histórico de mensagens não configurado")
        stored = await self._history.messages(conversation_id)
        return max((message.indice for message in stored), default=-1) + 1

    async def ingest(self, message: InboundMessage) -> bool:
        if self._closed:
            raise RuntimeError("Ingestão encerrada")
        resolution = await self._resolve(message)
        corpo = message.corpo
        if resolution is not None and resolution.transcricao is not None:
            # Áudio vira texto transcrito e redigido; o arquivo nunca é persistido.
            corpo = resolution.transcricao
            resolution = replace(resolution, transcricao=None)
        cpf_hash = self._privacy.cpf_hash(corpo)
        safe = replace(
            message, corpo=self._privacy.redact(corpo), media_ref=None, resolucao=resolution
        )
        cep = (
            self._capture_cep(message.corpo)
            if self._capture_cep and message.tipo == "text"
            else None
        )
        acceptance = asyncio.create_task(self._accept(safe, cpf_hash, cep))
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(acceptance)
                break
            except asyncio.CancelledError:
                cancelled = True
                if acceptance.done():
                    result = acceptance.result()
                    break
        if cancelled:
            raise asyncio.CancelledError()
        return result

    async def _resolve(self, message: InboundMessage) -> MediaResolution | None:
        # Documento nunca vai a provedor externo: ausência de chamada, não configuração.
        if self._media is None or message.tipo not in ("audio", "image") or not message.media_ref:
            return None
        try:
            async with asyncio.timeout(self._media_timeout):
                return await self._media.resolve(message)
        except ConfigurationError as error:
            # Não bloqueia o lead, mas é bug de deploy: registrado em ERROR com o corpo.
            logger.error("media_configuration %s", error.detalhe)
        except Exception as error:
            detail = getattr(error, "detalhe", None) or type(error).__name__
            logger.warning("media_unresolved %s %s", message.tipo, detail)
        return None

    async def _accept(self, safe: InboundMessage, cpf_hash: str | None, cep: str | None) -> bool:
        """Commit e enfileiramento completam juntos antes de cancelar o chamador."""
        key = safe.conversation_id
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            previous = self._workers.get(key)
            if previous is not None and previous.done():
                previous.result()
            conversation = await self._conversations.ensure(safe, cpf_hash, self._clock.now())
            if not await self._messages.add(safe, self._clock.now()):
                return False
            # Identidade técnica chega ao consumidor, nunca o telefone do canal.
            safe = replace(safe, channel_user_id=conversation.lead_id)
            self._pending.setdefault(key, []).append(safe)
            if cep is not None:
                self._private.setdefault(key, {})[safe.provider_message_id] = cep
            self._deadlines[key] = self._clock.monotonic() + self._window
            worker = self._workers.get(key)
            if worker is None or worker.done():
                if worker is not None:
                    worker.result()
                self._workers[key] = asyncio.create_task(self._run(key))
            return True

    async def _run(self, key: str) -> None:
        while self._pending.get(key) or key in self._ready:
            remaining = 0 if key in self._ready else self._deadlines[key] - self._clock.monotonic()
            if remaining > 0:
                await self._sleep(remaining)
                continue
            async with self._locks[key]:
                if key not in self._ready and self._clock.monotonic() < self._deadlines[key]:
                    continue
                if key not in self._ready:
                    messages = tuple(sorted(self._pending[key], key=lambda msg: msg.indice))
                    text = " ".join(msg.corpo for msg in messages if msg.tipo == "text")
                    if price_objection(text):
                        count = await self._conversations.add_price_objection(key)
                    else:
                        conversation = await self._reader.get(key)
                        if conversation is None:
                            raise RuntimeError("Conversa ausente na ingestão")
                        count = conversation.objecoes_preco
                    private = self._private.pop(key, {})
                    cep = next((private[msg.provider_message_id] for msg in messages
                                if msg.provider_message_id in private), None)
                    self._ready[key] = IngestedTurn(key, messages, count, cep)
                    del self._pending[key]
            # Um worker por conversa serializa consumo sem bloquear novas entradas.
            await self._consume(self._ready[key])
            del self._ready[key]

    async def wait_idle(self) -> None:
        """Barreira para replay; async with a executa automaticamente ao sair."""
        for key in self._ready.keys() | self._pending.keys():
            if key not in self._workers:
                self._workers[key] = asyncio.create_task(self._run(key))
        cancelled = False
        while self._workers:
            workers = dict(self._workers)
            completion = asyncio.gather(*workers.values(), return_exceptions=True)
            while True:
                try:
                    results = await asyncio.shield(completion)
                    break
                except asyncio.CancelledError:
                    cancelled = True
            for key, worker in workers.items():
                if self._workers.get(key) is worker:
                    del self._workers[key]
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        if cancelled:
            raise asyncio.CancelledError()
