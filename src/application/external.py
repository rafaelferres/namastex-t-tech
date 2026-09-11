"""Falhas de dependências externas que o sistema precisa distinguir entre si."""

from __future__ import annotations


class ConfigurationError(Exception):
    """A dependência rejeitou nossa configuração (credencial, rota, modelo, parâmetro).

    É bug de deploy: nunca vira indisponibilidade, erro de contrato ou fala de reserva.
    """

    def __init__(self, servico: str, detalhe: str) -> None:
        super().__init__(f"{servico}: {detalhe}")
        self.servico = servico
        self.detalhe = detalhe


class StartupCheckError(Exception):
    def __init__(self, falhas: dict[str, str]) -> None:
        resumo = "; ".join(f"{nome}: {detalhe}" for nome, detalhe in falhas.items())
        super().__init__(f"Configuração inválida na partida — {resumo}")
        self.falhas = falhas
