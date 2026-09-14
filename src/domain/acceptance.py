"""Projeção pura do catálogo para decisões de aceitação."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from domain._parsing import mapping, nonnegative_integer, sequence, string
from domain.quote import Declined, QuoteContractError, QuoteRequest


@dataclass(frozen=True, slots=True)
class FaixaAceitacao:
    minimo: int
    maximo: int
    motivo_recusa: str | None


def _parse_faixas(value: object, minimo: str, maximo: str) -> tuple[FaixaAceitacao, ...]:
    faixas = []
    for item in sequence(value):
        data = mapping(item)
        lower = nonnegative_integer(data[minimo])
        upper = nonnegative_integer(data[maximo])
        refused = data.get("recusar", False)
        if lower > upper or not isinstance(refused, bool):
            raise ValueError("Faixa de aceitação inválida")
        faixas.append(
            FaixaAceitacao(
                lower,
                upper,
                string(data["motivo"]) if refused else None,
            )
        )
    if not faixas:
        raise ValueError("Faixas de aceitação ausentes")
    return tuple(faixas)


def _evaluate_faixas(
    value: int,
    faixas: tuple[FaixaAceitacao, ...],
    motivo_fora: str,
) -> Declined | None:
    for faixa in faixas:
        if faixa.minimo <= value <= faixa.maximo:
            return Declined(faixa.motivo_recusa) if faixa.motivo_recusa is not None else None
    return Declined(motivo_fora)


@dataclass(frozen=True, slots=True)
class AcceptanceRules:
    planos_validos: frozenset[str]
    faixas_idade: tuple[FaixaAceitacao, ...]
    faixas_veiculo: tuple[FaixaAceitacao, ...]

    @classmethod
    def from_api(cls, payload: object) -> AcceptanceRules:
        """Seleciona limites e motivos sem guardar o payload original."""
        try:
            data = mapping(payload)
            regras = mapping(data["regras"])
            return cls(
                planos_validos=frozenset(
                    string(mapping(plano)["id"]) for plano in sequence(data["planos"])
                ),
                faixas_idade=_parse_faixas(regras["faixa_etaria"], "idade_min", "idade_max"),
                faixas_veiculo=_parse_faixas(regras["idade_veiculo"], "anos_min", "anos_max"),
            )
        except (KeyError, TypeError, ValueError):
            raise QuoteContractError("Catálogo de aceitação fora do contrato") from None

    def evaluate(self, req: QuoteRequest, hoje: date) -> Declined | None:
        if req.plano_id not in self.planos_validos:
            return Declined("Plano inexistente")
        return self.evaluate_profile(idade=req.idade, veiculo_ano=req.veiculo_ano, hoje=hoje)

    def evaluate_profile(
        self, *, idade: int | None, veiculo_ano: int | None, hoje: date
    ) -> Declined | None:
        """Julga só as dimensões conhecidas; a ausente não recusa nem aprova as demais."""
        if idade is not None:
            recusa = _evaluate_faixas(idade, self.faixas_idade, "Idade fora das faixas aceitas")
            if recusa is not None:
                return recusa
        if veiculo_ano is None:
            return None
        return _evaluate_faixas(
            max(0, hoje.year - veiculo_ano),
            self.faixas_veiculo,
            "Idade do veículo fora das faixas aceitas",
        )
