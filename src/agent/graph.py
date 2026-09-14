"""Orquestração de turno; decisões e apresentação permanecem determinísticas."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent.nodes.converse import ConversationInput, Converser, project_quote
from agent.nodes.extract import SlotExtractor, capture_private_cep
from agent.schemas.slots import Slots, SlotValue
from agent.templates import (
    render_declined,
    render_handoff,
    render_media_note,
    render_objection,
    render_quote,
    render_safe_reply,
    render_unavailable,
)
from application.external import ConfigurationError
from application.ingest import IngestedTurn
from application.llm import LLMContractError, LLMUnavailable, TokenBudgetExceeded
from application.ports import AcceptanceRulesProvider, Clock, QuoteProvider
from application.private_slots import PrivateCepStore
from application.tracing import ConversationAttemptsReader, turn_correlation
from application.turns import TurnEvent, TurnRecorder
from domain.handoff import (
    CollectedSlot,
    ConversationContext,
    HandoffDecision,
    HandoffPolicy,
    HandoffReason,
    HandoffSuggestion,
    SlotName,
)
from domain.messages import (
    ApresentarCotacao,
    Intent,
    MediaNote,
    MensagemConversacional,
    OutboundMessage,
    PedirDado,
)
from domain.objection import Objecao, objecao_lexical
from domain.product import ProductFacts
from domain.quote import Declined, Quote, QuoteRequest, QuoteUnavailable
from domain.scope import assunto_lexical
from infrastructure.privacy import PrivacyRedactor


@dataclass(frozen=True, slots=True)
class TurnConfig:
    # D-038: tetos de LLM no p99.9 por chamada, com um retry; o turno comporta o
    # caminho no p99.9 de cada etapa (7 + 7 + 3,5 s) e o retry usa o que sobra.
    budget_seconds: float = 18.0
    extraction_seconds: float = 7.0
    conversation_seconds: float = 7.0
    quote_seconds: float = 3.5

    def __post_init__(self) -> None:
        if any(not math.isfinite(value) or value <= 0 for value in asdict(self).values()):
            raise ValueError("Orçamentos devem ser finitos e positivos")


class TurnState(TypedDict, total=False):
    conversation_id: str
    message_id: str
    trace_id: str
    entrada: str
    historico: list[str]
    slots: dict[str, Any]
    status: str
    texto: str
    rota: list[str]
    tempos_ms: dict[str, float]
    inicio: float
    erro: str | None
    sugestao: str | None
    plano: str | None
    resultado: dict[str, Any] | None
    tool_result: dict[str, object] | None
    objecoes_preco: int
    tipo_midia: str | None
    pedido: str | None
    sem_avanco: int
    cep_coletado: bool
    objecao: str | None
    objecao_fonte: str | None
    nota_midia: str | None
    audios_sem_texto: int
    transcrito: bool
    assunto: str | None


type HandoffWriter = Callable[[str, str, HandoffDecision], Awaitable[None]]


def _confirmed[T](slot: SlotValue[T] | None) -> T | None:
    """Valor que sustenta recusa ou cotação: informado e digitado (ARQUITETURA §5).

    Incerto ou transcrito devolve None e o grafo pede confirmação; nunca é promovido.
    """
    if slot is None or slot.status != "informado" or slot.proveniencia != "digitado":
        return None
    return slot.valor


def _objection_values(entrada: str, model: Objecao | None) -> dict[str, str | None]:
    # Piso determinístico abaixo do sinal do modelo, mesmo desenho da regra de desconto.
    if model is not None:
        return {"objecao": model.value, "objecao_fonte": "modelo"}
    floor = objecao_lexical(entrada)
    if floor is None:
        return {"objecao": None, "objecao_fonte": None}
    return {"objecao": floor.value, "objecao_fonte": "lexico"}


class SalesGraph:
    def __init__(
        self,
        *,
        extractor: SlotExtractor,
        converser: Converser,
        quote: QuoteProvider,
        rules: AcceptanceRulesProvider,
        products: tuple[ProductFacts, ...],
        clock: Clock,
        handoff: HandoffWriter,
        traces: ConversationAttemptsReader,
        checkpointer: BaseCheckpointSaver[Any],
        config: TurnConfig,
        private_slots: PrivateCepStore | None = None,
        persona: str = "Atendimento claro e respeitoso",
        recorder: TurnRecorder | None = None,
    ) -> None:
        self._extractor, self._converser, self._quote = extractor, converser, quote
        self._rules, self._products, self._clock = rules, products, clock
        self._handoff, self._traces, self._config = handoff, traces, config
        self._private, self._persona = private_slots, persona
        self._policy = HandoffPolicy()
        self._recorder = recorder
        self._locks: dict[str, asyncio.Lock] = {}
        self._privacy = PrivacyRedactor()
        builder = StateGraph(TurnState)
        for name, node in (
            ("extract", self._extract),
            ("policy", self._evaluate),
            ("converse", self._converse),
            ("quote", self._get_quote),
            ("present", self._present),
            ("handoff", self._send_handoff),
            ("objection", self._objection),
        ):
            builder.add_node(name, node)
        builder.add_edge(START, "extract")
        builder.add_edge("extract", "policy")
        builder.add_conditional_edges("policy", self._route)
        builder.add_conditional_edges("converse", self._after_converse)
        builder.add_edge("quote", "present")
        builder.add_conditional_edges("present", self._after_present)
        builder.add_edge("handoff", END)
        builder.add_edge("objection", END)
        self._checkpointer = checkpointer
        self._graph = builder.compile(checkpointer=checkpointer)

    async def forget(self, conversation_id: str) -> None:
        """Retenção: o estado do grafo da conversa sai junto com os slots (D-036)."""
        async with self._locks.setdefault(conversation_id, asyncio.Lock()):
            await self._checkpointer.adelete_thread(conversation_id)

    async def turn(
        self,
        conversation_id: str,
        message_id: str,
        text: str,
        *,
        objecoes_preco: int = 0,
        tipo_midia: str | None = None,
        cep_coletado: bool = False,
        nota_midia: MediaNote | None = None,
        audios_sem_texto: int = 0,
        transcrito: bool = False,
    ) -> TurnState:
        async with self._locks.setdefault(conversation_id, asyncio.Lock()):
            config: RunnableConfig = {"configurable": {"thread_id": conversation_id}}
            previous = await self._graph.aget_state(config)
            if previous.values.get("message_id") == message_id and not previous.next:
                return cast(TurnState, previous.values)
            # CEP transcrito exige confirmação: nunca vira o CEP imutável sem ela.
            captured = None if transcrito else capture_private_cep(text)
            if captured is not None:
                if self._private is None:
                    raise RuntimeError("Armazenamento privado de CEP não configurado")
                await self._private.remember(conversation_id, captured)
                cep_coletado = True
            safe = self._privacy.redact(text)
            state: TurnState = {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "trace_id": f"{conversation_id}:{message_id}",
                "entrada": safe,
                "historico": [*previous.values.get("historico", []), safe],
                "inicio": self._clock.monotonic(),
                "status": "ativa",
                "texto": "",
                "rota": [],
                "tempos_ms": {},
                "erro": None,
                "sugestao": None,
                "resultado": None,
                "plano": None,
                "objecoes_preco": objecoes_preco,
                "tipo_midia": tipo_midia,
                "cep_coletado": cep_coletado or previous.values.get("cep_coletado", False),
                "objecao": None,
                "objecao_fonte": None,
                "nota_midia": nota_midia,
                "transcrito": transcrito,
                "audios_sem_texto": previous.values.get("audios_sem_texto", 0) + audios_sem_texto,
                "assunto": None,
            }
            return cast(TurnState, await self._graph.ainvoke(state, config))

    async def respond(self, turn: IngestedTurn) -> OutboundMessage:
        if not turn.messages:
            raise ValueError("Turno sem mensagens")
        if turn.private_cep is not None:
            if self._private is None:
                raise RuntimeError("Armazenamento privado de CEP não configurado")
            await self._private.remember(turn.conversation_id, turn.private_cep)
        messages = sorted(turn.messages, key=lambda item: item.indice)
        images = [item for item in messages if item.tipo == "image"]
        unresolved_audio = sum(item.tipo == "audio" and item.resolucao is None for item in messages)
        note: MediaNote | None = None
        if unresolved_audio:
            note = "audio_sem_texto"
        elif images:
            # Confiança baixa é "não sei": só alta confirma o veículo; nenhuma escala.
            seen = any(
                item.resolucao is not None
                and item.resolucao.e_veiculo is True
                and item.resolucao.confianca == "alta"
                for item in images
            )
            note = "foto_veiculo" if seen else "foto_neutra"
        state = await self.turn(
            turn.conversation_id,
            messages[-1].provider_message_id,
            "\n".join(item.corpo for item in messages),
            objecoes_preco=turn.objecoes_preco,
            tipo_midia="document" if any(item.tipo == "document" for item in messages) else None,
            cep_coletado=turn.private_cep is not None,
            nota_midia=note,
            audios_sem_texto=unresolved_audio,
            transcrito=any(
                item.tipo == "audio" and item.resolucao is not None for item in messages
            ),
        )
        payload = state.get("resultado")
        if state["status"] == "cotada" and payload is not None:
            quote = restore_quote(payload)
            facts = next(item for item in self._products if item.plano_id == quote.plano_id)
            return OutboundMessage(
                turn.conversation_id, Intent.APRESENTAR_COTACAO, ApresentarCotacao(quote, facts)
            )
        if state["status"] == "recusada":
            reason = (
                payload["motivo"] if payload is not None else state["texto"].split("Motivo: ")[-1]
            )
            return OutboundMessage(
                turn.conversation_id,
                Intent.RECUSAR,
                Declined(reason, origem="api" if payload else "regra_local"),
            )
        if state["status"] == "escalada":
            return OutboundMessage(
                turn.conversation_id, Intent.ESCALAR, await self._decision(state)
            )
        stored_note = cast("MediaNote | None", state.get("nota_midia"))
        if state.get("pedido"):
            request = PedirDado(cast(SlotName, state["pedido"]), nota=stored_note)
            return OutboundMessage(turn.conversation_id, Intent.PEDIR_DADO, request)
        text = state["texto"]
        if stored_note:
            text = f"{render_media_note(stored_note)} {text}".strip()
        return OutboundMessage(turn.conversation_id, Intent.CONVERSAR, MensagemConversacional(text))

    def _remaining(self, state: TurnState) -> float:
        return max(0.0, self._config.budget_seconds - (self._clock.monotonic() - state["inicio"]))

    def _event(
        self,
        state: TurnState,
        etapa: str,
        status: str,
        latency: float,
        erro: str | None,
        *,
        sugestao: str | None = None,
    ) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(
                TurnEvent(
                    state["trace_id"],
                    state["conversation_id"],
                    etapa,
                    status,
                    round(latency),
                    erro,
                    self._clock.now(),
                    sugestao=sugestao,
                )
            )
        except Exception:
            logging.getLogger(__name__).error("turn_trace_write_failed")

    def _external_failure(self, state: TurnState, node: str, error: BaseException) -> None:
        # Corpo redigido da falha externa vai para a timeline do turno (D-035).
        detail = getattr(error, "detalhe", None) or type(error).__name__
        self._event(state, f"{node}_falha", type(error).__name__, 0, detail)

    async def _generate[T](
        self, state: TurnState, node: str, ceiling: float, call: Callable[[float], Awaitable[T]]
    ) -> T:
        """Geração não tem efeito colateral: a chamada que estoura é refeita uma vez (D-038).

        Cada tentativa tem o teto da etapa e nunca passa do prazo do turno.
        """
        retried = False
        while True:
            budget = min(self._remaining(state), ceiling)
            try:
                async with asyncio.timeout(budget):
                    return await call(budget)
            except (LLMUnavailable, TimeoutError) as failure:
                self._external_failure(state, node, failure)
                if retried or self._remaining(state) <= 0:
                    raise
                retried = True

    def _update(self, state: TurnState, node: str, start: float, **values: Any) -> TurnState:
        latency = (self._clock.monotonic() - start) * 1000
        self._event(
            state,
            node,
            values.get("status", state["status"]),
            latency,
            values.get("erro", state.get("erro")),
        )
        return cast(
            TurnState,
            {
                "rota": [*state["rota"], node],
                "tempos_ms": {**state["tempos_ms"], node: latency},
                **values,
            },
        )

    async def _extract(self, state: TurnState) -> TurnState:
        start = self._clock.monotonic()
        slots = Slots.model_validate(state.get("slots", {}))
        try:
            result = await self._generate(
                state,
                "extract",
                self._config.extraction_seconds,
                lambda budget: self._extractor.extract(
                    state["entrada"],
                    slots,
                    conversation_id=state["conversation_id"],
                    budget=budget,
                    # Slot transcrito volta como confirmação antes de cotar (ARQUITETURA §5).
                    proveniencia="transcrito" if state.get("transcrito") else "digitado",
                ),
            )
            slots = result.slots
            error = "tokens" if result.tokens_esgotados else None
        except (LLMUnavailable, TimeoutError):
            error = "prazo" if self._remaining(state) <= 0 else "llm"
        except ConfigurationError as failure:
            self._external_failure(state, "extract", failure)
            raise
        return self._update(
            state,
            "extract",
            start,
            slots=slots.model_dump(mode="json", exclude={"cep"}),
            erro=error,
        )

    def _request(self, state: TurnState, plan: str) -> QuoteRequest | None:
        slots = Slots.model_validate(state.get("slots", {}))
        age, year = _confirmed(slots.idade), _confirmed(slots.veiculo_ano)
        beginning = _confirmed(slots.data_inicio)
        # Só valor confirmado vai à /quote; data incerta ("amanhã") nunca chega ao parse.
        if age is None or year is None or (slots.data_inicio is not None and beginning is None):
            return None
        return QuoteRequest(
            plan, age, year, data_inicio=date.fromisoformat(beginning) if beginning else None
        )

    async def _decision(self, state: TurnState) -> HandoffDecision:
        slots = Slots.model_validate(state.get("slots", {}))
        collected: dict[SlotName, CollectedSlot] = {}
        for name, item in slots.model_dump().items():
            if item is not None and item["valor"] is not None:
                collected[cast(SlotName, name)] = CollectedSlot(item["valor"], item["proveniencia"])
        try:
            attempts = await self._traces.read_conversation(state["conversation_id"])
        except Exception:
            logging.getLogger(__name__).error("turn_trace_read_failed")
            attempts = ()
        text = state["entrada"].casefold()
        suggestion = state.get("sugestao")
        subject = state.get("assunto")
        if subject in (None, "seguro_auto"):
            # Piso determinístico abaixo da categoria do modelo, mesmo desenho da objeção:
            # a política pede dado antes de o conversador falar, e o sinistro não pode esperar.
            subject = assunto_lexical(state["entrada"]) or "seguro_auto"
        # Só documento conta como mídia escalável aqui; áudio escala pelo acumulado.
        media = "documento" if state.get("tipo_midia") == "document" else None
        ctx = ConversationContext(
            slots=collected,
            tentativas=attempts,
            resultado_cotacao=QuoteUnavailable() if state.get("erro") == "quote" else None,
            tokens_esgotados=state.get("erro") == "tokens",
            llm_indisponivel=state.get("erro") == "llm",
            prazo_esgotado=state.get("erro") == "prazo",
            tipo_midia=cast(Any, media),
            audios_nao_resolvidos=state.get("audios_sem_texto", 0),
            objecoes_preco=state["objecoes_preco"],
            pede_humano=any(
                term in text
                for term in ("quero atendente", "falar com humano", "falar com uma pessoa")
            ),
            pede_desconto=any(
                term in text for term in ("quero desconto", "pode dar desconto", "tem desconto")
            ),
            sugestao_llm=HandoffSuggestion(True, HandoffReason(suggestion)) if suggestion else None,
            slot_em_esclarecimento=cast(SlotName | None, state.get("pedido")),
            tentativas_sem_avanco=state.get("sem_avanco", 0),
            assunto=cast(Any, subject),
        )
        return self._policy.evaluate(ctx)

    async def _evaluate(self, state: TurnState) -> TurnState:
        start = self._clock.monotonic()
        try:
            rules = await self._rules.current()
        except Exception:
            rules = None
        slots = Slots.model_validate(state.get("slots", {}))
        today = self._clock.today()
        age, year = _confirmed(slots.idade), _confirmed(slots.veiculo_ano)
        if rules is not None:
            # Recusa definitiva só com evidência confirmada; cada dimensão basta sozinha.
            declined = rules.evaluate_profile(idade=age, veiculo_ano=year, hoje=today)
            if declined:
                return self._update(
                    state,
                    "policy",
                    start,
                    status="recusada",
                    texto=render_declined(replace(declined, origem="regra_local")),
                    erro=None,
                )
        decision = await self._decision(state)
        if decision.escalar:
            return self._update(state, "policy", start, status="escalada")
        confirmed = {
            "idade": age,
            "veiculo_ano": year,
            "data_inicio": _confirmed(slots.data_inicio),
        }
        for name, value in confirmed.items():
            if value is None:
                wording = {
                    "idade": "sua idade",
                    "veiculo_ano": "o ano-modelo do veículo",
                    "data_inicio": "a data desejada para início da vigência",
                }[name]
                same = state.get("pedido") == name
                count = state.get("sem_avanco", 0) + 1 if same else 1
                return self._update(
                    state,
                    "policy",
                    start,
                    pedido=name,
                    sem_avanco=count,
                    texto=f"Pode informar ou confirmar {wording}?",
                )
        if year is not None and year > today.year + 1:
            return self._update(
                state,
                "policy",
                start,
                pedido="veiculo_ano",
                texto="Pode confirmar o ano-modelo do veículo?",
            )
        return self._update(state, "policy", start, pedido=None, sem_avanco=0)

    async def _route(self, state: TurnState) -> str:
        if state["status"] == "recusada":
            return END
        if state["status"] == "escalada":
            return "handoff"
        if state.get("pedido"):
            return END
        return "converse"

    async def _converse(self, state: TurnState) -> TurnState:
        start = self._clock.monotonic()
        try:
            result = await self._generate(
                state,
                "converse",
                self._config.conversation_seconds,
                lambda budget: self._converser.converse(
                    ConversationInput(
                        state["conversation_id"],
                        self._persona,
                        tuple(state["historico"]),
                        self._products,
                        state.get("tool_result"),
                    ),
                    budget=budget,
                ),
            )
            if result.violacao is not None:
                # Guardrail é evento observável com o texto ofensor, nunca exceção.
                logging.getLogger(__name__).warning("converser_guardrail_violation")
                latency = (self._clock.monotonic() - start) * 1000
                self._event(state, "guardrail", "violacao", latency, result.violacao)
            return self._update(
                state,
                "converse",
                start,
                plano=result.plano_id,
                texto=result.texto,
                sugestao=result.escalacao,
                assunto=result.assunto,
                **_objection_values(state["entrada"], result.objecao),
            )
        except ConfigurationError as failure:
            # Bug de deploy: registrado com o corpo e derruba o turno, sem fala de reserva.
            self._external_failure(state, "converse", failure)
            raise
        except LLMContractError as failure:
            # Erro do modelo (ex.: plano fora do catálogo) nunca vira recusa comercial.
            logging.getLogger(__name__).warning("converser_contract_error")
            self._external_failure(state, "converse", failure)
            return self._update(
                state,
                "converse",
                start,
                texto=render_safe_reply(self._products),
                erro="contrato_llm",
                **_objection_values(state["entrada"], None),
            )
        except TokenBudgetExceeded:
            error = "tokens"
        except (LLMUnavailable, TimeoutError):
            error = "prazo" if self._remaining(state) <= 0 else "llm"
        return self._update(state, "converse", start, erro=error, status="escalada")

    async def _after_converse(self, state: TurnState) -> str:
        decision = await self._decision(state)
        # As duas opiniões, sempre, inclusive sem escalação: a divergência é métrica (D-039).
        suggestion = state.get("sugestao")
        self._event(
            state,
            "decisao",
            decision.motivo.value if decision.motivo else "segue",
            0,
            None,
            sugestao=str(suggestion) if suggestion else None,
        )
        if decision.escalar:
            return "handoff"
        if state.get("plano"):
            return "quote"
        return "objection" if state.get("objecao") else END

    async def _get_quote(self, state: TurnState) -> TurnState:
        start = self._clock.monotonic()
        req = self._request(state, state.get("plano") or "")
        if req is None:
            raise LLMContractError()
        cep = await self._private.read(state["conversation_id"]) if self._private else None
        if state.get("cep_coletado") and cep is None:
            raise RuntimeError("CEP coletado indisponível; cotação não enviada")
        req = replace(req, cep=cep)
        try:
            # O turno empresta trace_id e conversa às tentativas: snapshot e timeline casam.
            with turn_correlation(state["trace_id"], state["conversation_id"]):
                async with asyncio.timeout(
                    min(self._remaining(state), self._config.quote_seconds)
                ):
                    result = await self._quote.quote(req)
            payload = json.loads(json.dumps(asdict(result), default=str))
            if payload.get("primeiro_pagamento_pro_rata") is None:
                payload.pop("primeiro_pagamento_pro_rata", None)
            payload["kind"] = "quote" if isinstance(result, Quote) else "declined"
            facts = next((item for item in self._products if item.plano_id == req.plano_id), None)
            projection = project_quote(result, facts) if facts else None
            return self._update(state, "quote", start, resultado=payload, tool_result=projection)
        except (QuoteUnavailable, TimeoutError):
            return self._update(
                state, "quote", start, erro="prazo" if self._remaining(state) <= 0 else "quote"
            )

    async def _present(self, state: TurnState) -> TurnState:
        start = self._clock.monotonic()
        payload = state.get("resultado")
        if payload is None:
            return self._update(
                state, "present", start, texto=render_unavailable(), status="escalada"
            )
        if payload["kind"] == "declined":
            return self._update(
                state,
                "present",
                start,
                texto=render_declined(Declined(payload["motivo"])),
                status="recusada",
            )
        quote = restore_quote(payload)
        facts = next(item for item in self._products if item.plano_id == quote.plano_id)
        return self._update(
            state, "present", start, texto=render_quote(quote, facts), status="cotada"
        )

    async def _after_present(self, state: TurnState) -> str:
        return "handoff" if state.get("erro") else END

    async def _send_handoff(self, state: TurnState) -> TurnState:
        start = self._clock.monotonic()
        decision = await self._decision(state)
        if not decision.escalar:
            raise RuntimeError("Rota de escalação sem decisão")
        await self._handoff(state["conversation_id"], state["trace_id"], decision)
        return self._update(
            state,
            "handoff",
            start,
            status="escalada",
            texto=render_unavailable() if state.get("erro") else render_handoff(),
        )

    async def _objection(self, state: TurnState) -> TurnState:
        objection = Objecao(cast(str, state.get("objecao")))
        # Categoria e fonte (modelo ou léxico) ficam na timeline para medir divergência.
        self._event(state, "objecao", state.get("objecao_fonte") or "lexico", 0, objection.value)
        return self._update(
            state, "objection", self._clock.monotonic(), texto=render_objection(objection)
        )


def restore_quote(payload: dict[str, Any]) -> Quote:
    restored = {
        **payload,
        "premio_mensal": Decimal(payload["premio_mensal"]),
        "franquia": Decimal(payload["franquia"]),
    }
    if "primeiro_pagamento_pro_rata" in payload:
        first = payload["primeiro_pagamento_pro_rata"]
        restored["primeiro_pagamento_pro_rata"] = {
            **first,
            "valor_primeiro_pagamento": Decimal(first["valor_primeiro_pagamento"]),
        }
    return Quote.from_api(restored)
