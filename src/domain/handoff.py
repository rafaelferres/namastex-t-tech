"""Política pura sobre sinais já extraídos; não classifica texto nem executa efeitos."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Literal, Protocol

from domain.quote import QuoteContractError, QuoteOutcome, QuoteUnavailable

type SlotName = Literal["plano_id", "idade", "veiculo_ano", "cep", "data_inicio"]


class HandoffReason(StrEnum):
    DOCUMENTO = "documento_recebido"
    MIDIA = "midia_nao_resolvida"
    COTACAO = "cotacao_esgotada"
    DESCONTO = "desconto_fora_da_tabela"
    HUMANO = "pedido_de_humano"
    LACO = "laco_no_slot"
    ESCOPO = "fora_de_escopo"
    TOKENS = "limite_de_tokens"


class QuoteAttemptView(Protocol):
    """Leitura dos registros existentes; domínio não depende da camada de aplicação."""

    @property
    def tentativa(self) -> int: ...

    @property
    def status(self) -> str: ...

    @property
    def latencia_ms(self) -> int: ...


@dataclass(frozen=True, slots=True)
class CollectedSlot:
    valor: str | int | date = field(repr=False)
    proveniencia: Literal["digitado", "transcrito"]


@dataclass(frozen=True, slots=True)
class HandoffSuggestion:
    escalar: bool
    motivo: HandoffReason | None = None


@dataclass(frozen=True, slots=True)
class ConversationContext:
    slots: Mapping[SlotName, CollectedSlot] = field(default_factory=dict)
    tentativas: tuple[QuoteAttemptView, ...] = ()
    tipo_midia: Literal["documento", "audio", "imagem"] | None = None
    midia_resolvida: bool = True
    # Somente o resultado final da cadeia, nunca uma falha intermediária do wire.
    resultado_cotacao: QuoteOutcome | QuoteUnavailable | QuoteContractError | None = None
    pede_desconto: bool = False
    objecoes_preco: int = 0
    pede_humano: bool = False
    tokens_esgotados: bool = False
    slot_em_esclarecimento: SlotName | None = None
    tentativas_sem_avanco: int = 0
    assunto: Literal[
        "seguro_auto", "sinistro", "cobranca", "cancelamento", "renovacao", "outro_ramo"
    ] = "seguro_auto"
    sugestao_llm: HandoffSuggestion | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", MappingProxyType(dict(self.slots)))
        object.__setattr__(self, "tentativas", tuple(self.tentativas))
        if self.tentativas_sem_avanco < 0:
            raise ValueError("Tentativas de esclarecimento não podem ser negativas")
        if self.objecoes_preco < 0:
            raise ValueError("Contagem de objeções não pode ser negativa")


@dataclass(frozen=True, slots=True)
class HandoffSnapshot:
    slots: Mapping[SlotName, CollectedSlot]
    tentativas: tuple[QuoteAttemptView, ...]
    motivo: HandoffReason

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", MappingProxyType(dict(self.slots)))
        object.__setattr__(self, "tentativas", tuple(self.tentativas))


@dataclass(frozen=True, slots=True)
class HandoffDecision:
    escalar: bool
    motivo: HandoffReason | None
    sugestao_llm: HandoffSuggestion | None
    divergencia: bool
    snapshot: HandoffSnapshot | None


def _decision(ctx: ConversationContext, reason: HandoffReason | None) -> HandoffDecision:
    suggestion = ctx.sugestao_llm
    positive = reason is not None
    llm_positive = suggestion.escalar if suggestion is not None else False
    disagreement = positive != llm_positive or (
        positive and suggestion is not None and suggestion.motivo != reason
    )
    snapshot = None
    if reason is not None:
        slots = dict(ctx.slots)
        if "cep" in slots:
            slots["cep"] = replace(slots["cep"], valor="[CEP REDIGIDO]")
        snapshot = HandoffSnapshot(slots, ctx.tentativas, reason)
    return HandoffDecision(positive, reason, suggestion, disagreement, snapshot)


class HandoffRule(Protocol):
    motivo: HandoffReason

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None: ...


class DocumentoRecebido:
    motivo = HandoffReason.DOCUMENTO

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return _decision(ctx, self.motivo) if ctx.tipo_midia == "documento" else None


class MidiaNaoResolvida:
    motivo = HandoffReason.MIDIA

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return (
            _decision(ctx, self.motivo)
            if ctx.tipo_midia in ("audio", "imagem") and not ctx.midia_resolvida
            else None
        )


class CotacaoEsgotada:
    motivo = HandoffReason.COTACAO

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return (
            _decision(ctx, self.motivo)
            if isinstance(ctx.resultado_cotacao, QuoteUnavailable)
            else None
        )


@dataclass(frozen=True, slots=True)
class DescontoForaTabela:
    limite: int = 3
    motivo: ClassVar[HandoffReason] = HandoffReason.DESCONTO

    def __post_init__(self) -> None:
        if type(self.limite) is not int or self.limite < 1:
            raise ValueError("Limiar de objeções deve ser inteiro positivo")

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return (
            _decision(ctx, self.motivo)
            if ctx.pede_desconto or ctx.objecoes_preco >= self.limite
            else None
        )


class OrcamentoTokensEsgotado:
    motivo = HandoffReason.TOKENS

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return _decision(ctx, self.motivo) if ctx.tokens_esgotados else None


class PedidoHumano:
    motivo = HandoffReason.HUMANO

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return _decision(ctx, self.motivo) if ctx.pede_humano else None


@dataclass(frozen=True, slots=True)
class LacoEsclarecimento:
    limite: int = 3
    motivo: ClassVar[HandoffReason] = HandoffReason.LACO

    def __post_init__(self) -> None:
        if type(self.limite) is not int or self.limite < 1:
            raise ValueError("Limiar do laço deve ser inteiro positivo")

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return (
            _decision(ctx, self.motivo)
            if ctx.slot_em_esclarecimento is not None and ctx.tentativas_sem_avanco >= self.limite
            else None
        )


class ForaEscopo:
    motivo = HandoffReason.ESCOPO

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision | None:
        return (
            _decision(ctx, self.motivo)
            if ctx.assunto in ("sinistro", "cobranca", "cancelamento", "renovacao", "outro_ramo")
            else None
        )


class HandoffPolicy:
    def __init__(self, rules: tuple[HandoffRule, ...] | None = None) -> None:
        self._rules = (
            rules
            if rules is not None
            else (
                DocumentoRecebido(),
                MidiaNaoResolvida(),
                CotacaoEsgotada(),
                OrcamentoTokensEsgotado(),
                DescontoForaTabela(),
                PedidoHumano(),
                LacoEsclarecimento(),
                ForaEscopo(),
            )
        )

    def evaluate(self, ctx: ConversationContext) -> HandoffDecision:
        for rule in self._rules:
            decision = rule.evaluate(ctx)
            if decision is not None:
                return decision
        return _decision(ctx, None)
