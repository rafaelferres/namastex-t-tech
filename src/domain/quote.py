"""Requisição, resultados e erros da cotação; nenhum cálculo de preço local."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from domain._parsing import mapping, money, nonnegative_integer, string, strings


class QuoteUnavailable(Exception):
    """Falha transitória de infraestrutura; pode ser retentada."""

    def __init__(
        self,
        message: str = "Serviço de cotação indisponível",
        *,
        suspeita_contrato: bool = False,
        ano_normalizado: bool = False,
        tentativas: int = 1,
        todas_falhas_suspeitas: bool | None = None,
        detalhe: str | None = None,
    ) -> None:
        super().__init__(message)
        self.suspeita_contrato = suspeita_contrato
        self.ano_normalizado = ano_normalizado
        self.tentativas = tentativas
        self.todas_falhas_suspeitas = (
            suspeita_contrato if todas_falhas_suspeitas is None else todas_falhas_suspeitas
        )
        # Status e corpo já redigidos pela folha; fica fora da mensagem e do traceback.
        self.detalhe = detalhe


class QuoteContractError(Exception):
    """Requisição ou resposta inválida; não deve ser retentada."""

    def __init__(
        self,
        message: str = "Cotação fora do contrato",
        *,
        ano_normalizado: bool = False,
        tentativas: int = 1,
        detalhe: str | None = None,
    ) -> None:
        super().__init__(message)
        self.ano_normalizado = ano_normalizado
        self.tentativas = tentativas
        self.detalhe = detalhe


class QuoteConfigurationError(QuoteContractError):
    """Rota ou credencial rejeitada pela API: bug de deploy, herda o não-retry do contrato."""


@dataclass(frozen=True, slots=True)
class Declined:
    motivo: str
    ano_normalizado: bool = field(default=False, kw_only=True, compare=False)
    origem: Literal["api", "cache", "regra_local"] = field(
        default="api", kw_only=True, compare=False
    )


@dataclass(frozen=True, slots=True)
class Carencia:
    coberturas: tuple[str, ...]
    dias: int
    observacao: str


@dataclass(frozen=True, slots=True)
class ProRata:
    dias_no_mes: int
    dias_cobrados: int
    valor_primeiro_pagamento: Decimal


@dataclass(frozen=True, slots=True)
class Quote:
    plano_id: str
    plano_nome: str
    premio_mensal: Decimal
    franquia: Decimal
    coberturas: tuple[str, ...]
    carencia: Carencia
    moeda: str
    primeiro_pagamento_pro_rata: ProRata | None = None
    ano_normalizado: bool = field(default=False, kw_only=True, compare=False)
    origem: Literal["api", "cache", "regra_local"] = field(
        default="api", kw_only=True, compare=False
    )

    @classmethod
    def from_api(cls, payload: object) -> Quote:
        try:
            data = mapping(payload)
            carencia = mapping(data["carencia"])
            pro_rata = None
            if "primeiro_pagamento_pro_rata" in data:
                first = mapping(data["primeiro_pagamento_pro_rata"])
                dias_mes = nonnegative_integer(first["dias_no_mes"])
                dias_cobrados = nonnegative_integer(first["dias_cobrados"])
                if not 28 <= dias_mes <= 31 or not 1 <= dias_cobrados <= dias_mes:
                    raise ValueError("Período inválido")
                pro_rata = ProRata(
                    dias_no_mes=dias_mes,
                    dias_cobrados=dias_cobrados,
                    valor_primeiro_pagamento=money(first["valor_primeiro_pagamento"]),
                )
            return cls(
                plano_id=string(data["plano_id"]),
                plano_nome=string(data["plano_nome"]),
                premio_mensal=money(data["premio_mensal"]),
                franquia=money(data["franquia"]),
                coberturas=strings(data["coberturas"]),
                carencia=Carencia(
                    coberturas=strings(carencia["coberturas"]),
                    dias=nonnegative_integer(carencia["dias"]),
                    observacao=string(carencia["observacao"]),
                ),
                moeda=string(data["moeda"]),
                primeiro_pagamento_pro_rata=pro_rata,
            )
        except (KeyError, ValueError, TypeError):
            raise QuoteContractError("Resposta de cotação fora do contrato") from None


type QuoteOutcome = Quote | Declined


@dataclass(frozen=True, slots=True)
class QuoteRequest:
    plano_id: str
    idade: int
    veiculo_ano: int
    cep: str | None = field(default=None, repr=False)
    data_inicio: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plano_id", self.plano_id.lower())
        if self.cep is not None:
            if not isinstance(self.cep, str):
                raise QuoteContractError("CEP inválido")
            normalized = self.cep.replace("-", "").replace(" ", "")
            if (
                not normalized.isascii()
                or not normalized.isdigit()
                or len(normalized) not in (7, 8)
            ):
                raise QuoteContractError("CEP inválido")
            object.__setattr__(self, "cep", normalized.zfill(8))

    def to_payload(self) -> dict[str, str | int]:
        payload: dict[str, str | int] = {
            "plano_id": self.plano_id,
            "idade": self.idade,
            "veiculo_ano": self.veiculo_ano,
        }
        if self.cep is not None:
            payload["cep"] = self.cep
        if self.data_inicio is not None:
            payload["data_inicio"] = self.data_inicio.isoformat()
        return payload

    def fingerprint(self, dia_referencia: date) -> str:
        canonical = json.dumps(
            [
                dia_referencia.isoformat(),
                self.plano_id,
                self.idade,
                self.veiculo_ano,
                self.cep,
                self.data_inicio.isoformat() if self.data_inicio else None,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
