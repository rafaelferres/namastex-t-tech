from __future__ import annotations

import pytest

from domain.handoff import ConversationContext, DescontoForaTabela, HandoffDecision
from domain.messages import InboundMessage, Intent, OutboundMessage, PedirDado


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
