"""Seis objeções canônicas e o piso lexical abaixo da classificação do modelo."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class Objecao(StrEnum):
    CARO_PARA_O_CARRO = "caro_para_o_carro"
    FRANQUIA_ALTA = "franquia_alta"
    PRECO_ALTO = "preco_alto"
    CONCORRENTE_MAIS_BARATO = "concorrente_mais_barato"
    CONSULTAR_FAMILIA = "consultar_familia"
    PRECISA_PENSAR = "precisa_pensar"


# Ordem importa: a frase principal vence o sufixo "... a X me ofereceu menos".
_FLOOR = tuple(
    (objecao, re.compile(pattern))
    for objecao, pattern in (
        (Objecao.FRANQUIA_ALTA, r"\bfranquia\b.{0,20}\b(?:alta|cara|salgada|puxada)\b"),
        (
            Objecao.CARO_PARA_O_CARRO,
            r"\bcar[oa] (?:pra|para) (?:esse|este|o|meu)\b.{0,12}\bcarro\b",
        ),
        (
            Objecao.PRECO_ALTO,
            r"\b(?:preco|valor)\b.{0,20}\b(?:salgado|alto|puxado)\b"
            r"|\bcar[oa]\b|\bpuxad[oa]\b|\bfora do (?:meu )?orcamento\b",
        ),
        (
            Objecao.CONSULTAR_FAMILIA,
            r"\bver com (?:a |o )?(?:minha |meu )?(?:esposa|marido|mulher|familia|pai|mae)\b",
        ),
        (Objecao.PRECISA_PENSAR, r"\b(?:preciso|vou|deixa eu) pensar\b"),
        (
            Objecao.CONCORRENTE_MAIS_BARATO,
            r"\bmais barat[oa]\b|\bconcorren\w*|\bme ofereceu menos\b",
        ),
    )
)
_NEGATED_PRICE = re.compile(r"\bnao\s+(?:(?:esta|e|achei|ficou|ta)\s+)?car[oa]\b")


def objecao_lexical(text: str) -> Objecao | None:
    """Piso determinístico: frases conhecidas são reconhecidas mesmo sem o modelo."""
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    normalized = _NEGATED_PRICE.sub("", normalized)
    return next((objecao for objecao, pattern in _FLOOR if pattern.search(normalized)), None)
