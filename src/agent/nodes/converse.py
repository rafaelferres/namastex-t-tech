"""Linguagem recebe projeções; apresentação financeira continua no template."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.prompts.converser import CONVERSER_PROMPT
from application.llm import LLMClient, LLMContractError, LLMRequest, LLMRole, LLMTool
from domain.handoff import HandoffReason
from domain.product import ProductFacts
from domain.quote import Declined, Quote, QuoteOutcome, QuoteUnavailable
from infrastructure.privacy import PrivacyRedactor


@dataclass(frozen=True, slots=True)
class ConversationInput:
    conversation_id: str
    persona: str
    historico: tuple[str, ...]
    produtos: tuple[ProductFacts, ...]
    resultado: dict[str, object] | None = None


class ConversationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    texto: str
    escalacao: HandoffReason | None


@dataclass(frozen=True, slots=True)
class ConversationResult:
    texto: str
    escalacao: HandoffReason | None
    plano_id: str | None = None


def project_quote(
    result: QuoteOutcome | QuoteUnavailable, facts: ProductFacts
) -> dict[str, object]:
    return {
        "status": "cotado"
        if isinstance(result, Quote)
        else ("recusado" if isinstance(result, Declined) else "indisponivel"),
        "nome_plano": facts.nome,
        "coberturas": list(facts.coberturas),
        "carencia": facts.tem_carencia,
    }


_NUMBERS = re.compile(
    r"\d|R\$|\b(?:reais|real|centavos?|mil|milhão|milhões|cem|cento|duzentos|trezentos|"
    r"quatrocentos|quinhentos|seiscentos|setecentos|oitocentos|novecentos|dez|vinte|trinta|"
    r"quarenta|cinquenta|sessenta|setenta|oitenta|noventa)\b",
    re.IGNORECASE,
)
# Financial prose belongs to templates even when it contains no digits or currency.
_FINANCIAL = re.compile(
    r"R\$|\b(?:pr[eê]mio\w*|franquia\w*|pre[çc]o\w*|valor\w*|reais|real|"
    r"cust\w*|pag\w*|parcel\w*|gratuit\w*|mensal\w*|mensais|"
    r"cobran[çc]\w*|cobrar\w*|descont\w*)\b|"
    r"\bpor\s+m[eê]s\b|\b(?:sai|sair|fica|ficar)\s+por\b",
    re.IGNORECASE,
)
_DECIMAL_AMOUNT = re.compile(r"\d+[.,]\d+")


class Converser:
    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._privacy = PrivacyRedactor()

    async def converse(
        self, context: ConversationInput, *, budget: float = 3.0
    ) -> ConversationResult:
        history = [
            self._privacy.redact(text)
            for text in context.historico
            if not _FINANCIAL.search(text) and not _DECIMAL_AMOUNT.search(text)
        ]
        products = [
            {
                "plano_id": item.plano_id,
                "nome": item.nome,
                "coberturas": list(item.coberturas),
                "carencia": item.tem_carencia,
            }
            for item in context.produtos
        ]
        projection = None
        if context.resultado is not None:
            allowed = {"status", "nome_plano", "coberturas", "carencia"}
            if set(context.resultado) != allowed:
                raise LLMContractError()
            projection = context.resultado
        tool = LLMTool(
            "cotar",
            "Solicita cotação do plano selecionado",
            {
                "type": "object",
                "properties": {"plano_id": {"type": "string"}},
                "required": ["plano_id"],
                "additionalProperties": False,
            },
        )
        request = LLMRequest(
            context.conversation_id,
            LLMRole.CONVERSATION,
            CONVERSER_PROMPT + "\nPersona: " + self._privacy.redact(context.persona),
            json.dumps(
                {"historico": history, "produtos": products, "resultado": projection},
                ensure_ascii=False,
            ),
            ConversationOutput.model_json_schema(),
            budget,
            tools=(tool,),
        )
        response = await self._client.complete(request)
        if response.tool_calls:
            if len(response.tool_calls) != 1:
                raise LLMContractError()
            call = response.tool_calls[0]
            plan = call.arguments.get("plano_id")
            if (
                call.name != "cotar"
                or set(call.arguments) != {"plano_id"}
                or not isinstance(plan, str)
                or not plan
            ):
                raise LLMContractError()
            return ConversationResult("", None, plan)
        try:
            parsed = ConversationOutput.model_validate_json(response.content)
        except ValidationError:
            raise LLMContractError() from None
        if _NUMBERS.search(parsed.texto) or _FINANCIAL.search(parsed.texto):
            raise LLMContractError()
        return ConversationResult(self._privacy.redact(parsed.texto), parsed.escalacao)
