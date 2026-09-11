from __future__ import annotations

import pytest

from tests.golden.harness import Case, ExtractedSlots, NullExtractor, cases_from_rows, evaluate


@pytest.mark.asyncio
async def test_metrics_score_each_slot_and_extractor_receives_only_messages() -> None:
    class CannedExtractor:
        async def extract(self, messages: tuple[str, ...]) -> ExtractedSlots:
            assert messages in (("primeiro",), ("segundo",))
            return ExtractedSlots(30, "Fiat Mobi 2020")

    cases = (
        Case("a", ("primeiro",), 30, "Fiat Mobi 2020", "2026-01-01", "sem_resposta"),
        Case("b", ("segundo",), 40, "Fiat Mobi 2020", "2026-01-01", "ganho"),
    )
    report = await evaluate(cases, CannedExtractor())
    assert report.total == 2
    assert report.idade_accuracy == 0.5
    assert report.veiculo_accuracy == 1.0
    baseline = await evaluate(cases, NullExtractor())
    assert baseline.idade_accuracy == baseline.veiculo_accuracy == 0.0


@pytest.mark.asyncio
async def test_empty_evaluation_is_explicit() -> None:
    with pytest.raises(ValueError, match="vazio"):
        await evaluate((), NullExtractor())


def test_case_loader_redacts_and_never_supplies_seller_quotes() -> None:
    common = dict(
        conversation_id="a",
        timestamp="2026-02-01T10:00:00",
        lead_idade_informada=30,
        veiculo_texto="Fiat Mobi 2020",
        conversation_outcome="ganho",
        message_type="text",
    )
    cases = cases_from_rows(
        [
            dict(common, message_index=1, sender_role="vendedor", message_body="R$ 999,00"),
            dict(
                common, message_index=0, sender_role="lead", message_body="email teste@example.com"
            ),
        ]
    )
    assert cases[0].messages == ("email [EMAIL]",)
    assert cases[0].reference_date == "2026-02-01"
