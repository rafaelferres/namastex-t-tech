from __future__ import annotations

import pytest

from domain.scope import assunto_lexical


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bati o carro ontem e quero abrir um sinistro", "sinistro"),
        ("Quero cancelar meu seguro", "cancelamento"),
        ("Como faço o cancelamento da apólice?", "cancelamento"),
        ("Não recebi o boleto da cobrança deste mês", "cobranca"),
        ("Quando é a renovação do meu seguro?", "renovacao"),
        ("Vocês fazem seguro de vida?", "outro_ramo"),
        ("Oi, queria fazer um seguro pro meu carro", None),
        ("Tenho 32 anos, Onix 2020, cep [CEP]", None),
        ("Achei caro, a Porto Seguro me ofereceu menos", None),
    ],
)
def test_floor_recognizes_out_of_scope_subjects_without_the_model(text, expected):
    assert assunto_lexical(text) == expected
