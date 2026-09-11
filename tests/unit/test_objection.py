from __future__ import annotations

import pytest

from domain.objection import Objecao, objecao_lexical
from tests.fakes import CANONICAL_OBJECTIONS

EXPECTED = dict(
    zip(
        CANONICAL_OBJECTIONS,
        (
            Objecao.CARO_PARA_O_CARRO,
            Objecao.FRANQUIA_ALTA,
            Objecao.PRECO_ALTO,
            Objecao.CONCORRENTE_MAIS_BARATO,
            Objecao.CONSULTAR_FAMILIA,
            Objecao.PRECISA_PENSAR,
        ),
        strict=True,
    )
)
COMPETITORS = ("Azul Seguros", "Bradesco Auto", "Itau Auto", "Porto Seguro", "SulAmerica")


@pytest.mark.parametrize("text", CANONICAL_OBJECTIONS)
def test_each_canonical_objection_has_its_category(text):
    assert objecao_lexical(text) is EXPECTED[text]


@pytest.mark.parametrize("competitor", COMPETITORS)
@pytest.mark.parametrize("text", CANONICAL_OBJECTIONS)
def test_competitor_suffix_keeps_primary_category(text, competitor):
    assert objecao_lexical(f"{text}... a {competitor} me ofereceu menos") is EXPECTED[text]


@pytest.mark.parametrize("text", ["tá puxado", "Fora do meu orçamento", "O VALOR TÁ ALTO"])
def test_known_paraphrases_hit_price_floor(text):
    assert objecao_lexical(text) is Objecao.PRECO_ALTO


@pytest.mark.parametrize(
    "text",
    [
        "Ola! Vi o anuncio de voces, quanto fica o seguro?",
        "Oi, queria fazer um seguro pro meu carro",
        "fechado!",
        "pode emitir entao",
        "nao precisa, obrigado",
        "não achei caro",
        "esperava menos",  # lacuna do piso: só o modelo reconhece
    ],
)
def test_non_objections_and_floor_gaps_stay_unclassified(text):
    assert objecao_lexical(text) is None
