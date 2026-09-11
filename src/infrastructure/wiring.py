from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import httpx

from agent.graph import SalesGraph, TurnConfig
from agent.nodes.converse import ConversationInput, Converser
from agent.nodes.extract import SlotExtractor, capture_private_cep
from agent.schemas.slots import Slots
from application.ingest import IngestedTurn, Ingestor
from application.inspect_conversation import InspectConversation
from application.inspect_trace import InspectQuoteTrace
from application.llm import LLMClient
from application.media import MediaResolver
from application.ports import (
    AcceptanceRulesProvider,
    AttemptRecorder,
    Clock,
    QuoteCache,
    QuoteProvider,
)
from application.sales import SalesSession
from application.startup import verify_dependencies
from application.tracing import Correlation, CorrelationProvider, current_turn
from domain.handoff import HandoffDecision
from infrastructure.llm.budget import BudgetedLLMClient
from infrastructure.llm.config import LLMConfig
from infrastructure.llm.http import OpenRouterLLMClient
from infrastructure.media.resolver import LLMMediaResolver
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.checkpoint import open_checkpointer, open_turn_states
from infrastructure.persistence.connection import connect
from infrastructure.persistence.conversations import SQLiteConversations
from infrastructure.persistence.delivery import SQLiteDelivery
from infrastructure.persistence.quote_cache import SQLiteQuoteCache
from infrastructure.persistence.turns import SQLiteTurnEvents
from infrastructure.planos.client import PlanosClient
from infrastructure.privacy import PrivacyRedactor, install_redacting_logging
from infrastructure.quote.cache import CachingQuoteProvider
from infrastructure.quote.config import QuoteConfig
from infrastructure.quote.guard import EligibilityGuardProvider
from infrastructure.quote.hedge import HedgingQuoteProvider
from infrastructure.quote.http import HttpQuoteProvider
from infrastructure.quote.retry import RetryingQuoteProvider
from infrastructure.quote.trace import ApplicationTrace, WireTrace
from infrastructure.tracing.correlation import ContextCorrelationProvider
from infrastructure.tracing.recorder import BufferedAttemptRecorder


def build_quote_provider(
    *,
    client: httpx.AsyncClient,
    cache: QuoteCache,
    rules: AcceptanceRulesProvider,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]],
    rng: Callable[[], float],
    recorder: AttemptRecorder,
    correlation: CorrelationProvider,
    config: QuoteConfig | None = None,
) -> QuoteProvider:
    config = config if config is not None else QuoteConfig()
    http = HttpQuoteProvider(
        client, timeout=config.timeout, clock=clock, observe_status=correlation.observe_http
    )
    wire = WireTrace(http, recorder, correlation, clock)
    hedge = HedgingQuoteProvider(wire, hedge_delay=config.hedge_delay, sleep=sleep)
    retry = RetryingQuoteProvider(
        hedge,
        max_attempts=config.max_attempts,
        base_delay=config.base_delay,
        max_delay=config.max_delay,
        budget=config.budget,
        sleep=sleep,
        rng=rng,
        clock=clock,
        contract_threshold=config.contract_threshold,
    )
    cached = CachingQuoteProvider(retry, cache, clock)
    guard = EligibilityGuardProvider(cached, rules, clock)
    return ApplicationTrace(guard, recorder, correlation, clock)


def build_ingestor(
    connection: sqlite3.Connection,
    consume: Callable[[IngestedTurn], Awaitable[None]],
    *,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]],
    window: float = 0.5,
    capture_cep: Callable[[str], str | None] | None = None,
    media: MediaResolver | None = None,
) -> Ingestor:
    privacy = PrivacyRedactor()
    install_redacting_logging(logging.getLogger(), privacy)
    store = SQLiteConversations(connection)
    return Ingestor(
        store,
        store,
        store,
        privacy,
        consume,
        clock=clock,
        sleep=sleep,
        window=window,
        capture_cep=capture_cep,
        media=media,
    )


@asynccontextmanager
async def conversation_inspector(path: Path) -> AsyncIterator[InspectConversation]:
    """Conversa inteira, turno a turno; tudo aberto em modo somente leitura."""
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False
    )
    try:
        async with open_turn_states(path) as states:
            delivery = SQLiteDelivery(connection)
            yield InspectConversation(
                states, SQLiteAttempts(connection), SQLiteTurnEvents(connection), delivery, delivery
            )
    finally:
        connection.close()


@contextmanager
def trace_inspector(path: Path) -> Iterator[InspectQuoteTrace]:
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False
    )
    try:
        has_turns = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_events'"
        ).fetchone()
        yield InspectQuoteTrace(
            SQLiteAttempts(connection), SQLiteTurnEvents(connection) if has_turns else None
        )
    finally:
        connection.close()


def build_llm_client(*, client: httpx.AsyncClient, config: LLMConfig, clock: Clock) -> LLMClient:
    return BudgetedLLMClient(
        OpenRouterLLMClient(client, config, clock), config.conversation_token_limit
    )


def build_slot_extractor(
    *,
    client: httpx.AsyncClient,
    config: LLMConfig,
    clock: Clock,
    privacy: PrivacyRedactor,
) -> SlotExtractor:
    return SlotExtractor(
        build_llm_client(client=client, config=config, clock=clock),
        privacy,
        default_budget=config.budget_seconds,
    )


@dataclass(frozen=True, slots=True)
class SalesStack:
    graph: SalesGraph
    session: SalesSession
    ingestor: Ingestor


def _turn_correlation() -> Correlation:
    current = current_turn()
    if current is None:
        raise RuntimeError("Cotação fora de um turno de conversa")
    return current


@asynccontextmanager
async def open_sales_stack(
    database: Path,
    *,
    quote_client: httpx.AsyncClient,
    extractor: SlotExtractor,
    converser: Converser,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]],
    rng: Callable[[], float],
    turn_config: TurnConfig,
    quote_config: QuoteConfig | None = None,
    window: float = 0.5,
    after_reply: Callable[[str], None] | None = None,
    verify: bool = True,
    media: LLMMediaResolver | None = None,
    # Janela de atendimento do WhatsApp: passadas 24 h sem mensagem do lead, a conversa
    # não recebe mais texto livre; os slots saem com ela (D-038).
    retention: timedelta = timedelta(hours=24),
) -> AsyncIterator[SalesStack]:
    """Composição única do agente; uma conexão SQLite por papel, no mesmo arquivo."""
    conversations, outbox, trace, cache, events, private = (
        connect(database) for _ in range(6)
    )
    try:
        attempts = SQLiteAttempts(trace)
        planos = PlanosClient(quote_client, clock=clock, ttl=300.0, timeout=2.0)

        async def probe_converser() -> object:
            facts = (await planos.get()).product_facts
            probe = ConversationInput("verificacao-partida", "", ("Olá",), facts)
            return await converser.converse(probe, budget=turn_config.conversation_seconds)

        if verify:
            # Mesmos parâmetros de produção: o 404 do conversador morreria aqui (D-035).
            await verify_dependencies(
                {
                    **({"llm_midia": media.probe} if media is not None else {}),
                    "api_cotacao": planos.get,
                    "llm_extrator": lambda: extractor.extract(
                        "Olá",
                        Slots(),
                        conversation_id="verificacao-partida",
                        budget=turn_config.extraction_seconds,
                    ),
                    "llm_conversador": probe_converser,
                }
            )
        products = (await planos.get()).product_facts
        quote = build_quote_provider(
            client=quote_client,
            cache=SQLiteQuoteCache(cache, clock),
            rules=planos,
            clock=clock,
            sleep=sleep,
            rng=rng,
            recorder=BufferedAttemptRecorder(attempts.record),
            # Tentativas herdam o trace_id do turno: snapshot e timeline leem o mesmo id.
            correlation=ContextCorrelationProvider(_turn_correlation),
            config=quote_config,
        )
        delivery = SQLiteDelivery(outbox)

        async def handoff(conversation_id: str, trace_id: str, decision: HandoffDecision) -> None:
            # Decisão e efeitos persistidos juntos, antes de qualquer entrega.
            await delivery.enqueue_handoff(
                conversation_id, decision, clock.now(), identifier=trace_id
            )

        turn_events = SQLiteTurnEvents(events)
        slots = SQLiteConversations(private)  # CEP durável e purga no encerramento
        async with open_checkpointer(database) as checkpointer:
            graph = SalesGraph(
                extractor=extractor,
                converser=converser,
                quote=quote,
                rules=planos,
                products=products,
                clock=clock,
                handoff=handoff,
                traces=attempts,
                checkpointer=checkpointer,
                config=turn_config,
                private_slots=slots,
                recorder=turn_events,
            )
            session = SalesSession(graph, delivery, clock, closer=slots, retention=retention)
            await session.purge()  # na partida; a cada turno, o próprio consume purga

            async def consume(turn: IngestedTurn) -> None:
                await session.consume(turn)
                if after_reply is not None:
                    after_reply(turn.conversation_id)

            async with build_ingestor(
                conversations,
                consume,
                clock=clock,
                sleep=sleep,
                window=window,
                capture_cep=capture_private_cep,
                media=media,
            ) as ingestor:
                yield SalesStack(graph, session, ingestor)
            await turn_events.drain()
    finally:
        for connection in (conversations, outbox, trace, cache, events, private):
            connection.close()
