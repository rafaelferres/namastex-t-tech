from __future__ import annotations

import json
from pathlib import Path

from interfaces.replay import envelopes_from_rows


def test_replay_orders_indices_filters_sellers_and_preserves_media() -> None:
    rows = [
        dict(conversation_id="a", message_index=8, timestamp="2026-01-01T08:00",
             sender_role="lead", message_type="audio", message_body="[audio] voz"),
        dict(conversation_id="a", message_index=1, timestamp="2026-01-01T09:00",
             sender_role="lead", message_type="text", message_body="olá"),
        dict(conversation_id="a", message_index=2, sender_role="vendedor"),
        dict(conversation_id="b", message_index=0, sender_role="lead"),
    ]
    messages = envelopes_from_rows(rows, {"a"})
    assert [message.indice for message in messages] == [1, 8]
    assert messages[0].channel == "replay"
    assert messages[0].channel_user_id == "a"
    assert messages[1].media_status == "nao_resolvido"
    assert messages[1].provider_message_id == "a:8"


def test_real_fixture_replays_original_indices_despite_timestamps() -> None:
    rows = json.loads((Path(__file__).parents[1] / "fixtures/evaluation/replay.json").read_text())
    timestamps = [row["timestamp"] for row in rows]
    assert timestamps != sorted(timestamps)
    conversation_id = rows[0]["conversation_id"]
    messages = envelopes_from_rows(reversed(rows), {conversation_id})
    assert [message.indice for message in messages] == [row["message_index"] for row in rows]
    assert all(message.channel_user_id == conversation_id for message in messages)
