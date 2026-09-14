from __future__ import annotations

from typing import get_args

import pytest

from agent.templates import render_media_note, render_objection, render_safe_reply
from domain.handoff import ConversationContext, DescontoForaTabela, HandoffDecision
from domain.messages import (
    InboundMessage,
    Intent,
    MediaNote,
    MensagemConversacional,
    OutboundMessage,
    PedirDado,
)
from domain.objection import Objecao
from domain.product import ProductFacts


@pytest.mark.parametrize(
    "text",
    ["O seguro custa cinco reais por mês.", "Desconto de 15% no Premium.", "Franquia de mil."],
)
def test_conversational_envelope_cannot_carry_quantity(text: str) -> None:
    with pytest.raises(ValueError) as error:
        MensagemConversacional(text)
    assert text not in str(error.value)


def test_every_template_that_becomes_conversation_is_quantity_free() -> None:
    facts = (
        ProductFacts("essencial", "Essencial", ("colisao",), False),
        ProductFacts("premium", "Premium", ("assistencia_24h",), True),
    )
    texts = [render_objection(item) for item in Objecao]
    texts += [render_media_note(note) for note in get_args(MediaNote.__value__)]
    texts.append(render_safe_reply(facts))
    for text in texts:
        assert MensagemConversacional(text).texto == text


def test_media_envelope_is_unresolved_and_output_is_intention() -> None:
    incoming = InboundMessage("replay", "conv_1", "lead_1", "audio", "", "msg_1", 3)
    assert incoming.media_status == "nao_resolvido"
    outgoing = OutboundMessage("conv_1", Intent.PEDIR_DADO, PedirDado("idade"))
    assert outgoing.payload.slot == "idade"
    with pytest.raises(ValueError):
        OutboundMessage("conv_1", Intent.RECUSAR, PedirDado("idade"))


def test_price_objections_escalate_without_model_at_configurable_floor() -> None:
    rule = DescontoForaTabela(limite=3)
    assert rule.evaluate(ConversationContext(objecoes_preco=2)) is None
    result = rule.evaluate(ConversationContext(objecoes_preco=3))
    assert result is not None and result.escalar and result.divergencia
    assert DescontoForaTabela(limite=2).evaluate(ConversationContext(objecoes_preco=2))


def test_invalid_outbound_intent_has_generic_error_without_raw_input() -> None:
    with pytest.raises(ValueError) as error:
        OutboundMessage("conv_1", "teste@example.test", PedirDado("idade"))  # type: ignore[arg-type]
    assert "teste@example.test" not in str(error.value)


@pytest.mark.parametrize("conversation_id", ["", "   "])
def test_outbound_requires_conversation_identity(conversation_id: str) -> None:
    with pytest.raises(ValueError):
        OutboundMessage(conversation_id, Intent.PEDIR_DADO, PedirDado("idade"))


def test_pedir_dado_rejects_unknown_slot_without_exposing_input() -> None:
    with pytest.raises(ValueError) as error:
        PedirDado("teste@example.test")  # type: ignore[arg-type]
    assert "teste@example.test" not in str(error.value)


def test_escalar_requires_positive_handoff_decision() -> None:
    negative = HandoffDecision(False, None, None, False, None)
    with pytest.raises(ValueError):
        OutboundMessage("conv_1", Intent.ESCALAR, negative)
    positive = DescontoForaTabela().evaluate(ConversationContext(objecoes_preco=3))
    assert positive is not None
    assert OutboundMessage("conv_1", Intent.ESCALAR, positive).payload is positive
