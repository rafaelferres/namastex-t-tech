"""Renderização pura. Redação provisória, a refinar em sessão dedicada."""

from __future__ import annotations

from decimal import Decimal

from domain.product import ProductFacts
from domain.quote import Declined, Quote, QuoteContractError

_COVERAGES = {
    "colisao": "colisão",
    "roubo": "roubo",
    "furto": "furto",
    "terceiros": "danos a terceiros",
    "vidros": "vidros",
    "carro_reserva": "carro reserva",
    "assistencia_24h": "assistência 24 horas",
}


def format_brl(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise QuoteContractError("Valor monetário inválido para apresentação")
    whole, _, fraction = format(value, "f").partition(".")
    if any(digit != "0" for digit in fraction[2:]):
        raise QuoteContractError("Valor monetário não representável em centavos")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"R$ {grouped},{fraction.ljust(2, '0')[:2]}"


def _coverage_list(codes: tuple[str, ...]) -> str:
    try:
        names = [_COVERAGES[code] for code in codes]
    except KeyError:
        raise QuoteContractError("Cobertura sem descrição aprovada") from None
    if not names:
        raise QuoteContractError("Coberturas ausentes")
    return ", ".join(names[:-1]) + " e " + names[-1] if len(names) > 1 else names[0]


def render_quote(payload: object, facts: ProductFacts) -> str:
    quote = payload if isinstance(payload, Quote) else Quote.from_api(payload)
    if quote.moeda != "BRL" or quote.plano_id != facts.plano_id:
        raise QuoteContractError("Plano ou moeda divergente na apresentação")
    required = set(quote.coberturas) & {"roubo", "furto"}
    if required and (quote.carencia.dias <= 0 or not required <= set(quote.carencia.coberturas)):
        raise QuoteContractError("Carência obrigatória ausente na cotação")
    lines = [
        f"Plano {facts.nome}",
        f"Mensalidade: {format_brl(quote.premio_mensal)}.",
        f"Franquia: {format_brl(quote.franquia)}.",
        f"Coberturas: {_coverage_list(quote.coberturas)}.",
    ]
    if quote.carencia.dias > 0 and quote.carencia.coberturas:
        lines.append(
            f"Carência de {quote.carencia.dias} dias para "
            f"{_coverage_list(quote.carencia.coberturas)}, contada do início da vigência."
        )
    if quote.primeiro_pagamento_pro_rata is not None:
        first = quote.primeiro_pagamento_pro_rata
        lines.append(
            f"Primeiro mês (pro-rata): {format_brl(first.valor_primeiro_pagamento)}, "
            f"referente a {first.dias_cobrados} dias de um mês de {first.dias_no_mes} dias."
        )
    return "\n".join(lines)


def render_declined(declined: Declined) -> str:
    return f"Não podemos oferecer uma cotação para este perfil. Motivo: {declined.motivo}"


def render_unavailable() -> str:
    return (
        "Não foi possível concluir sua cotação agora. "
        "Para continuar, precisamos da ajuda de um atendente."
    )


def render_handoff() -> str:
    return (
        "Seu atendimento precisa continuar com uma pessoa da equipe. "
        "As informações já coletadas acompanham o encaminhamento."
    )
