from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent.prompts.extractor import EXTRACTOR_PROMPT
from agent.schemas.slots import Proveniencia, Slots
from application.llm import (
    LLMClient,
    LLMContractError,
    LLMRequest,
    LLMRole,
    LLMUnavailable,
    TokenBudgetExceeded,
)
from infrastructure.llm.recording import LLMFixtureInvalid, LLMFixtureMissing
from infrastructure.privacy import PrivacyRedactor

_CEP_DIGITS = r"[0-9]{2}\.?[0-9]{2,3}[- ]?[0-9]{3}"
_LABELED_CEP = re.compile(r"\bCEP\s*[:=]?\s*(" + _CEP_DIGITS + r")(?!\w)", re.IGNORECASE)
_BARE_CEP = re.compile(r"^\s*(" + _CEP_DIGITS + r")\s*$")
_PUNCTUATED_CEP = re.compile(r"(?<!\w)[0-9]{2}\.[0-9]{3}[- ]?[0-9]{3}(?!\w)")
_LABELED_MONEY = re.compile(
    r"\b(?:pr[eê]mio|franquia|pro[- ]?rata|pre[cç]o|mensalidade)\s*(?:[ée](?:\s+de)?|de|[:=])?\s*"
    r"(?:R\$\s*)?[0-9][0-9.,]*",
    re.IGNORECASE,
)
_MONEY = re.compile(r"(?:R\$|BRL)\s*[0-9][0-9.,]*|[0-9][0-9.,]*\s*reais\b", re.IGNORECASE)


def capture_private_cep(message: str) -> str | None:
    """Read an explicitly labeled or standalone CEP without exposing it to the model."""
    match = _LABELED_CEP.search(message) or _BARE_CEP.fullmatch(message)
    return re.sub(r"\D", "", match[1]).zfill(8) if match else None


def _strict_schema() -> dict[str, Any]:
    schema = Slots.model_json_schema()

    def require_properties(node: object) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                require_properties(value)
        elif isinstance(node, list):
            for value in node:
                require_properties(value)

    require_properties(schema)
    return schema


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    slots: Slots
    tokens_esgotados: bool = False


class ExtractionUnavailable(LLMUnavailable):
    def __init__(self, slots: Slots) -> None:
        super().__init__()
        self._slots = slots

    @property
    def slots(self) -> Slots:
        return self._slots


class ExtractionContractError(LLMContractError):
    def __init__(self, slots: Slots) -> None:
        super().__init__()
        self._slots = slots

    @property
    def slots(self) -> Slots:
        return self._slots


class SlotExtractor:
    def __init__(
        self, client: LLMClient, privacy: PrivacyRedactor, default_budget: float = 2.5
    ) -> None:
        self._client = client
        self._privacy = privacy
        self._default_budget = default_budget

    async def extract(
        self,
        message: str,
        slots: Slots,
        *,
        conversation_id: str,
        proveniencia: Proveniencia = "digitado",
        budget: float | None = None,
    ) -> ExtractionResult:
        private_cep = capture_private_cep(message)
        preserved = slots.model_dump(mode="json")
        can_capture_cep = slots.cep is None or slots.cep.valor is None
        can_confirm_cep = (
            slots.cep is not None
            and slots.cep.status == "incerto"
            and private_cep == slots.cep.valor
            and proveniencia == "digitado"
        )
        if private_cep is not None and (can_capture_cep or can_confirm_cep):
            preserved["cep"] = {
                "valor": private_cep,
                "status": "incerto" if proveniencia == "transcrito" else "informado",
                "proveniencia": proveniencia,
            }
        slots = Slots.model_validate(preserved)
        message = _LABELED_CEP.sub("CEP [CEP]", message)
        message = _BARE_CEP.sub("[CEP]", message)
        public_slots = slots.model_dump(mode="json")
        public_slots["cep"] = None
        payload = json.dumps({"mensagem": message, "slots": public_slots}, ensure_ascii=False)
        payload = _MONEY.sub("[VALOR]", self._privacy.redact(payload))
        payload = _LABELED_MONEY.sub("[VALOR]", payload)
        payload = _PUNCTUATED_CEP.sub("[CEP]", payload)
        request = LLMRequest(
            conversation_id=conversation_id,
            role=LLMRole.EXTRACTOR,
            system=EXTRACTOR_PROMPT,
            user=payload,
            schema=_strict_schema(),
            budget=self._default_budget if budget is None else budget,
        )
        try:
            response = await self._client.complete(request)
        except TokenBudgetExceeded:
            return ExtractionResult(slots=slots, tokens_esgotados=True)
        except (LLMFixtureMissing, LLMFixtureInvalid):
            raise
        except LLMUnavailable:
            raise ExtractionUnavailable(slots) from None
        except LLMContractError:
            raise ExtractionContractError(slots) from None
        try:
            extracted = Slots.model_validate_json(response.content)
        except ValidationError:
            raise ExtractionContractError(slots) from None
        merged = slots.model_dump(mode="json")
        for name, value in extracted.model_dump(mode="json").items():
            if name == "cep" or value is None:
                continue
            value["proveniencia"] = proveniencia
            if proveniencia == "transcrito":
                value["status"] = "incerto"
            merged[name] = value
        return ExtractionResult(slots=Slots.model_validate(merged))
