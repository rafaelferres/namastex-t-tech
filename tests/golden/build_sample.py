"""Regenerate only sanitized fixtures from the local corpus; prints aggregate counts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from domain.acceptance import AcceptanceRules
from infrastructure.privacy import PrivacyRedactor
from interfaces.replay import dataset_path, read_rows
from tests.golden.harness import cases_from_rows
from tests.regression.oracle import stratified_sample


def main() -> None:
    fixtures = Path(__file__).parents[1] / "fixtures"
    rows = read_rows(dataset_path())
    rules = AcceptanceRules.from_api(json.loads((fixtures / "plans.json").read_text()))
    sample = stratified_sample(cases_from_rows(rows), rules)
    (fixtures / "evaluation/sample.json").write_text(
        json.dumps([asdict(case) for case in sample], ensure_ascii=False, indent=2) + "\n"
    )
    redactor = PrivacyRedactor()
    # Select the first actual lead stream with nonmonotonic timestamps.
    conversation_id = next(
        cid
        for cid in sorted({str(row["conversation_id"]) for row in rows})
        if (
            timestamps := [
                str(row["timestamp"])
                for row in rows
                if row["conversation_id"] == cid and row["sender_role"] == "lead"
            ]
        )
        != sorted(timestamps)
    )
    replay = [
        {
            key: redactor.redact(str(row[key])) if key == "message_body" else row[key]
            for key in (
                "conversation_id",
                "message_index",
                "timestamp",
                "sender_role",
                "message_type",
                "message_body",
            )
        }
        for row in rows
        if row["conversation_id"] == conversation_id and row["sender_role"] == "lead"
    ]
    (fixtures / "evaluation/replay.json").write_text(
        json.dumps(replay, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Sanitized sample: {len(sample)} cases; replay: {len(replay)} messages")


if __name__ == "__main__":
    main()
