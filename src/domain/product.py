from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductFacts:
    plano_id: str
    nome: str
    coberturas: tuple[str, ...]
    tem_carencia: bool
