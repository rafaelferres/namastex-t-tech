from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from application.tracing import QuoteAttempt
from domain.handoff import (
    CollectedSlot,
    ConversationContext,
    CotacaoEsgotada,
    DescontoForaTabela,
    DocumentoRecebido,
    ForaEscopo,
    HandoffPolicy,
    HandoffReason,
    HandoffSuggestion,
    LacoEsclarecimento,
    MidiaNaoResolvida,
    PedidoHumano,
)
from domain.quote import Declined, QuoteContractError, QuoteUnavailable


@pytest.mark.parametrize(
    "rule, changes, reason",
    [
        (DocumentoRecebido(), {"tipo_midia": "documento"}, HandoffReason.DOCUMENTO),
        # Só o N-ésimo áudio sem texto escala; imagem nunca escala (test_media).
        (MidiaNaoResolvida(), {"audios_nao_resolvidos": 2}, HandoffReason.MIDIA),
        (
            CotacaoEsgotada(),
            {"resultado_cotacao": QuoteUnavailable(tentativas=3)},
            HandoffReason.COTACAO,
        ),
        (DescontoForaTabela(), {"pede_desconto": True}, HandoffReason.DESCONTO),
        (PedidoHumano(), {"pede_humano": True}, HandoffReason.HUMANO),
        (
            LacoEsclarecimento(3),
            {"slot_em_esclarecimento": "idade", "tentativas_sem_avanco": 3},
            HandoffReason.LACO,
        ),
        (ForaEscopo(), {"assunto": "sinistro"}, HandoffReason.ESCOPO),
    ],
)
def test_each_rule_matches_only_its_condition(rule, changes, reason) -> None:
    assert rule.evaluate(ConversationContext()) is None
    decision = rule.evaluate(ConversationContext(**changes))
    assert decision is not None and decision.escalar
    assert decision.motivo == reason == rule.motivo


@pytest.mark.parametrize(
    "result", [Declined("Idade acima do limite", origem="regra_local"), QuoteContractError()]
)
def test_refusal_and_contract_bug_are_not_escalation(result) -> None:
    decision = HandoffPolicy().evaluate(ConversationContext(resultado_cotacao=result))
    assert not decision.escalar and decision.motivo is None


@pytest.mark.parametrize(
    "topic", ["sinistro", "cobranca", "cancelamento", "renovacao", "outro_ramo"]
)
def test_out_of_scope_topics(topic: str) -> None:
    assert (
        HandoffPolicy().evaluate(ConversationContext(assunto=topic)).motivo == HandoffReason.ESCOPO
    )


def test_llm_cannot_escalate_without_rule() -> None:
    suggestion = HandoffSuggestion(True, HandoffReason.HUMANO)
    decision = HandoffPolicy().evaluate(ConversationContext(sugestao_llm=suggestion))
    assert not decision.escalar and decision.divergencia
    assert decision.sugestao_llm is suggestion


@pytest.mark.parametrize(
    "suggestion, divergence",
    [
        (None, True),
        (HandoffSuggestion(True, HandoffReason.HUMANO), False),
        (HandoffSuggestion(False), True),
        (HandoffSuggestion(True, HandoffReason.DESCONTO), True),
    ],
)
def test_policy_keeps_both_opinions(suggestion, divergence: bool) -> None:
    decision = HandoffPolicy().evaluate(
        ConversationContext(pede_humano=True, sugestao_llm=suggestion)
    )
    assert decision.escalar and decision.motivo == HandoffReason.HUMANO
    assert decision.sugestao_llm == suggestion
    assert decision.divergencia == divergence


def test_loop_threshold_and_reset_after_progress() -> None:
    rule = LacoEsclarecimento(4)
    context = ConversationContext(slot_em_esclarecimento="idade", tentativas_sem_avanco=3)
    assert rule.evaluate(context) is None
    assert rule.evaluate(replace(context, tentativas_sem_avanco=4)) is not None
    assert rule.evaluate(replace(context, tentativas_sem_avanco=0)) is None
    assert (
        rule.evaluate(replace(context, slot_em_esclarecimento=None, tentativas_sem_avanco=4))
        is None
    )


def test_order_is_explicit_and_stops_at_first_match() -> None:
    context = ConversationContext(tipo_midia="documento", pede_humano=True)
    assert HandoffPolicy().evaluate(context).motivo == HandoffReason.DOCUMENTO
    assert (
        HandoffPolicy((PedidoHumano(), DocumentoRecebido())).evaluate(context).motivo
        == HandoffReason.HUMANO
    )


def test_snapshot_reuses_attempt_records_and_preserves_provenance_without_full_postcode() -> None:
    attempt = QuoteAttempt(
        "trace",
        "conv",
        "hash",
        1,
        "unavailable",
        "api",
        503,
        25,
        False,
        False,
        "QuoteUnavailable",
        datetime(2026, 9, 11, tzinfo=UTC),
    )
    slots = {
        "idade": CollectedSlot(30, "digitado"),
        "veiculo_ano": CollectedSlot(2026, "transcrito"),
        "cep": CollectedSlot("01310100", "digitado"),
    }
    context = ConversationContext(slots=slots, tentativas=(attempt,), pede_humano=True)
    decision = HandoffPolicy().evaluate(context)
    assert decision.snapshot is not None
    assert decision.snapshot.slots["idade"] == slots["idade"]
    assert decision.snapshot.slots["veiculo_ano"].proveniencia == "transcrito"
    assert decision.snapshot.slots["cep"].valor == "[CEP REDIGIDO]"
    assert context.slots["cep"].valor == "01310100"
    assert decision.snapshot.tentativas[0] is attempt
    assert decision.snapshot.motivo == HandoffReason.HUMANO
    slots.clear()
    assert len(decision.snapshot.slots) == 3
    with pytest.raises(TypeError):
        decision.snapshot.slots["idade"] = CollectedSlot(99, "digitado")
