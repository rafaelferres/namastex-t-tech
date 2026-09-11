from __future__ import annotations

from domain.handoff import ConversationContext, HandoffPolicy, HandoffReason


def test_token_exhaustion_is_deterministic_handoff_without_model_suggestion():
    result = HandoffPolicy().evaluate(ConversationContext(tokens_esgotados=True))
    assert result.escalar
    assert result.motivo == HandoffReason.TOKENS
    assert result.divergencia
    assert not HandoffPolicy().evaluate(ConversationContext()).escalar
