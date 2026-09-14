"""Quantidade em fala livre: numeral, moeda, porcentagem ou valor zero (D-042).

Classe fechada da língua, não lista de exemplos: todo dígito Unicode, todo cardinal
por extenso e as unidades que transformam uma palavra em valor.
"""

from __future__ import annotations

import re
import unicodedata

# "um" e "uma" ficam de fora por serem artigo; valor com eles exige unidade ou outro numeral.
_CARDINAIS = frozenset(
    """
    zero dois duas tres quatro cinco seis sete oito nove dez onze doze treze catorze quatorze
    quinze dezesseis dezasseis dezessete dezassete dezoito dezenove dezanove vinte trinta
    quarenta cinquenta sessenta setenta oitenta noventa cem cento duzentos duzentas trezentos
    trezentas quatrocentos quatrocentas quinhentos quinhentas seiscentos seiscentas setecentos
    setecentas oitocentos oitocentas novecentos novecentas mil milhao milhoes bilhao bilhoes
    dezena dezenas centena centenas milhar milhares duzia duzias metade dobro triplo terco
    real reais centavo centavos
    """.split()
)
_EXPRESSOES = re.compile(
    r"r\$|%|\bbrl\b|\bpor\s*cento\b|\bgratis\b|\bgratuit[oa]s?\b|\bde\s+graca\b|\bsem\s+custo\b"
)
# Único numeral tolerado: o nome aprovado da cobertura, o mesmo que o template escreve.
_COBERTURA = re.compile(r"\bassistencia\s+24\s*h(?:oras)?\b")


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(char for char in decomposto if not unicodedata.combining(char))


def contem_quantidade(texto: str) -> bool:
    normalizado = _COBERTURA.sub(" ", _normalizar(texto))
    if any(unicodedata.numeric(char, None) is not None for char in normalizado):
        return True
    return _EXPRESSOES.search(normalizado) is not None or not _CARDINAIS.isdisjoint(
        re.findall(r"\w+", normalizado)
    )
