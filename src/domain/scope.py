"""Assunto da mensagem: categoria do modelo com piso lexical abaixo, como a objeção."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

Assunto = Literal["seguro_auto", "sinistro", "cobranca", "cancelamento", "renovacao", "outro_ramo"]

# Palavras que não aparecem num pedido de cotação (zero nas mensagens de lead do dataset).
_FLOOR: tuple[tuple[Assunto, re.Pattern[str]], ...] = (
    ("sinistro", re.compile(r"\bsinistr\w*|\bacionar (?:o )?seguro\b")),
    ("cancelamento", re.compile(r"\bcancel\w*")),
    ("cobranca", re.compile(r"\bcobranc\w*|\bboleto\w*|\bfatura\w*|\bsegunda via\b")),
    ("renovacao", re.compile(r"\brenova\w*")),
    (
        "outro_ramo",
        re.compile(
            r"\bseguro (?:de )?(?:vida|residencia\w*|viagem|saude|celular|empresarial)\b"
        ),
    ),
)


def assunto_lexical(text: str) -> Assunto | None:
    """Piso determinístico: o assunto fora de escopo é reconhecido mesmo sem o modelo."""
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    return next((assunto for assunto, pattern in _FLOOR if pattern.search(normalized)), None)
