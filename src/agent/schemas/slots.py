from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, StrictInt, StrictStr, model_validator

Proveniencia = Literal["digitado", "transcrito"]


def _normalize_cep(value: str) -> str:
    digits = re.sub(r"[ .-]", "", value)
    if not re.fullmatch(r"[0-9]{7,8}", digits):
        raise ValueError("CEP inválido")
    return digits.zfill(8)


Cep = Annotated[StrictStr, AfterValidator(_normalize_cep)]


class SlotValue[T](BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    valor: T | None
    status: Literal["informado", "incerto"]
    proveniencia: Proveniencia

    @model_validator(mode="after")
    def informed_requires_value(self) -> Self:
        if self.status == "informado" and self.valor is None:
            raise ValueError("Slot informado exige valor")
        return self


class Slots(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    plano_id: SlotValue[StrictStr] | None = None
    idade: SlotValue[StrictInt] | None = None
    veiculo_ano: SlotValue[StrictInt] | None = None
    cep: SlotValue[Cep] | None = None
    data_inicio: SlotValue[StrictStr] | None = None

    @model_validator(mode="after")
    def informed_date_requires_iso(self) -> Self:
        slot = self.data_inicio
        if slot is not None and slot.status == "informado":
            if slot.valor is None or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", slot.valor):
                raise ValueError("Data de início deve ser ISO válida")
            try:
                date.fromisoformat(slot.valor)
            except ValueError:
                raise ValueError("Data de início deve ser ISO válida") from None
        return self
