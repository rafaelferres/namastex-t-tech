"""Verificação de partida: uma chamada mínima por dependência externa."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping

from application.external import ConfigurationError, StartupCheckError

logger = logging.getLogger(__name__)

type Probe = Callable[[], Awaitable[object]]


async def verify_dependencies(probes: Mapping[str, Probe]) -> dict[str, str]:
    """Configuração errada falha alto; indisponibilidade passageira só é registrada."""
    report: dict[str, str] = {}
    failures: dict[str, str] = {}
    for name, probe in probes.items():
        try:
            await probe()
        except ConfigurationError as error:
            failures[name] = error.detalhe
            logger.error("startup_check_configuration %s %s", name, error.detalhe)
        except Exception as error:
            detail = getattr(error, "detalhe", None) or type(error).__name__
            report[name] = "indisponivel"
            logger.warning("startup_check_unavailable %s %s", name, detail)
        else:
            report[name] = "ok"
    if failures:
        raise StartupCheckError(failures)
    return report
