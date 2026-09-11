from __future__ import annotations

import pytest

from domain.handoff import ConversationContext, HandoffPolicy, HandoffReason


@pytest.mark.parametrize(
    "flag,reason",
    [
        ("llm_indisponivel", "linguagem_indisponivel"),
        ("prazo_esgotado", "prazo_do_turno"),
    ],
)
def test_turn_failures_are_not_quote_failures(flag, reason):
    decision = HandoffPolicy().evaluate(ConversationContext(**{flag: True}))
    assert decision.escalar
    assert decision.motivo == reason
    assert decision.motivo != HandoffReason.COTACAO
    assert not HandoffPolicy().evaluate(ConversationContext()).escalar
