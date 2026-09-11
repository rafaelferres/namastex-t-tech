"""Parquet boundary adapter; seller messages never enter agent input."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Mapping, Set
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

from domain.messages import InboundMessage, MessageType
from infrastructure.privacy import PrivacyRedactor


def dataset_path() -> Path:
    return Path(
        os.environ.get(
            "AUTOSEGURO_DATASET", "../namastex-fde-challenge/dataset/conversations.parquet"
        )
    )


def read_rows(path: Path) -> list[dict[str, object]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    return cast(list[dict[str, object]], pq.read_table(path).to_pylist())


def envelopes_from_rows(
    rows: Iterable[Mapping[str, object]],
    conversations: Set[str] | None = None,
) -> tuple[InboundMessage, ...]:
    selected = [
        row
        for row in rows
        if row["sender_role"] == "lead"
        and (conversations is None or str(row["conversation_id"]) in conversations)
    ]
    selected.sort(key=lambda row: (str(row["conversation_id"]), int(str(row["message_index"]))))
    return tuple(
        InboundMessage(
            channel="replay",
            conversation_id=str(row["conversation_id"]),
            channel_user_id=str(row["conversation_id"]),
            tipo=cast(MessageType, row["message_type"]),
            corpo=str(row["message_body"]),
            provider_message_id=f"{row['conversation_id']}:{row['message_index']}",
            indice=int(str(row["message_index"])),
        )
        for row in selected
    )


def load_replay(
    path: Path,
    conversations: Set[str] | None = None,
) -> tuple[InboundMessage, ...]:
    """Raw envelopes are only suitable for the privacy-aware ingestion boundary."""
    return envelopes_from_rows(read_rows(path), conversations)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=dataset_path())
    parser.add_argument("--conversation", action="append", required=True)
    args = parser.parse_args()
    if not args.dataset.is_file():
        parser.error("Dataset local ausente; configure AUTOSEGURO_DATASET ou --dataset")
    redactor = PrivacyRedactor()
    for message in load_replay(args.dataset, set(args.conversation)):
        safe = replace(message, corpo=redactor.redact(message.corpo))
        print(json.dumps(asdict(safe), ensure_ascii=False))


if __name__ == "__main__":
    main()
