"""Linguagem recebe projeções; apresentação financeira continua no template."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.prompts.converser import CONVERSER_PROMPT
from agent.templates import render_safe_reply
from application.llm import LLMClient, LLMContractError, LLMRequest, LLMRole, LLMTool
from domain.handoff import HandoffReason
from domain.objection import Objecao
from domain.product import ProductFacts
from domain.quantidade import contem_quantidade
from domain.quote import Declined, Quote, QuoteOutcome, QuoteUnavailable
from domain.scope import Assunto
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
    # Obrigatório no schema strict; roteia para o nó de objeção junto com o piso lexical.
    objecao: Objecao | Literal["nenhuma"]
    # Liga a regra "fora de escopo"; o grafo cai no piso lexical quando o modelo não fala.
    assunto: Assunto


@dataclass(frozen=True, slots=True)
class ConversationResult:
    texto: str
    escalacao: HandoffReason | None
    plano_id: str | None = None
    # Fala do modelo descartada pelo guardrail, já redigida; o grafo registra no trace.
    violacao: str | None = None
    objecao: Objecao | None = None
    assunto: Assunto | None = None


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


class Converser:
    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._privacy = PrivacyRedactor()

    async def converse(
        self, context: ConversationInput, *, budget: float = 3.0
    ) -> ConversationResult:
        # Filtro direcional: a fala do lead chega inteira, só redigida; a saída é filtrada.
        history = [self._privacy.redact(text) for text in context.historico]
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
        plans = [item.plano_id for item in context.produtos]
        tool = LLMTool(
            "cotar",
            "Solicita cotação do plano selecionado",
            {
                "type": "object",
                "properties": {"plano_id": {"type": "string", "enum": plans}},
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
            # Plano fora do catálogo é erro do modelo, nunca pedido de cotação.
            if (
                call.name != "cotar"
                or set(call.arguments) != {"plano_id"}
                or not isinstance(plan, str)
                or plan not in plans
            ):
                raise LLMContractError()
            return ConversationResult("", None, plan)
        try:
            parsed = ConversationOutput.model_validate_json(response.content)
        except ValidationError:
            raise LLMContractError() from None
        text = self._privacy.redact(parsed.texto)
        objection = parsed.objecao if isinstance(parsed.objecao, Objecao) else None
        # Fala livre não carrega quantidade (D-042): valor ao lead só sai do payload da
        # /quote pelo template; MensagemConversacional recusa o mesmo texto por construção.
        if contem_quantidade(text):
            return ConversationResult(
                render_safe_reply(context.produtos),
                parsed.escalacao,
                violacao=text,
                objecao=objection,
                assunto=parsed.assunto,
            )
        return ConversationResult(
            text, parsed.escalacao, objecao=objection, assunto=parsed.assunto
        )
