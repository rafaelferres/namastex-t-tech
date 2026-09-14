from __future__ import annotations

import pytest

from domain.quantidade import contem_quantidade


@pytest.mark.parametrize(
    "text",
    [
        # dígitos, com e sem moeda, inclusive fora do ASCII
        "Fica R$ 313,80.",
        "Sai por 99.",
        "Parcelo em 3x.",
        "Desconto de 15%.",
        "Custa ٣ reais.",
        "Metade: ½.",
        # por extenso, em cada ordem de grandeza
        "Custa cinco reais.",
        "Uns dois ou três.",
        "Doze por mês.",
        "Dezessete mensais.",
        "Sai por noventa.",
        "Cento e vinte.",
        "Quinhentas pratas.",
        "Quatro mil de franquia.",
        "Um milhão.",
        "Custa zero.",
        # moeda e porcentagem sem numeral
        "Custa um real.",
        "Menos de um centavo.",
        "Dez por cento.",
        "Uma dúzia de parcelas.",
        # fração, múltiplo e valor zero
        "Sai pela metade do preço.",
        "O dobro do Essencial.",
        "O primeiro mês é grátis.",
        "Assistência sem custo.",
        "Vidros de graça.",
        "Cobertura gratuita.",
        # caixa e grafia
        "TRÊS parcelas.",
        "Cinqüenta.",
    ],
)
def test_quantity_in_any_form_is_detected(text: str) -> None:
    assert contem_quantidade(text)


@pytest.mark.parametrize(
    "text",
    [
        "Qual plano você prefere: Essencial, Completo ou Premium?",
        "Um plano com menos coberturas pode fazer sentido para uma família.",
        "O primeiro pagamento considera o início da vigência.",
        "O Premium inclui assistência 24 horas.",
        "Tem assistência 24h.",
        "Posso seguir em dezembro, setembro ou novembro.",
        "A franquia é mais baixa no Premium.",
        "Centro, cemitério e milho não são números.",
        "Carência vale para roubo e furto.",
    ],
)
def test_number_free_speech_is_not_a_quantity(text: str) -> None:
    assert not contem_quantidade(text)
