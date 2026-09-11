from __future__ import annotations

from dataclasses import dataclass

from domain._parsing import mapping, nonnegative_integer, sequence, string, strings
from domain.acceptance import AcceptanceRules
from domain.quote import QuoteContractError


@dataclass(frozen=True, slots=True)
class ProductFacts:
    plano_id: str
    nome: str
    coberturas: tuple[str, ...]
    tem_carencia: bool


@dataclass(frozen=True, slots=True)
class Planos:
    acceptance_rules: AcceptanceRules
    product_facts: tuple[ProductFacts, ...]


def project_planos(payload: object) -> Planos:
    """Retém somente aceitação e fatos permitidos; não armazena o payload."""
    try:
        data = mapping(payload)
        regras = mapping(data["regras"])
        carencia = mapping(regras["carencia"])
        dias = nonnegative_integer(carencia["dias"])
        coberturas_com_carencia = strings(carencia["coberturas_com_carencia"])
        products = []
        for item in sequence(data["planos"]):
            plano = mapping(item)
            coberturas = strings(plano["coberturas"])
            products.append(
                ProductFacts(
                    plano_id=string(plano["id"]),
                    nome=string(plano["nome"]),
                    coberturas=coberturas,
                    tem_carencia=dias > 0 and any(c in coberturas_com_carencia for c in coberturas),
                )
            )
        if not products or len({product.plano_id for product in products}) != len(products):
            raise ValueError("Catálogo vazio ou com planos duplicados")
        return Planos(
            acceptance_rules=AcceptanceRules.from_api(payload),
            product_facts=tuple(products),
        )
    except (KeyError, TypeError, ValueError, QuoteContractError):
        raise QuoteContractError("Catálogo de planos fora do contrato") from None
