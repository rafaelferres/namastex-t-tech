from __future__ import annotations

import logging
from dataclasses import replace

from application.external import ConfigurationError
from application.ports import AcceptanceRulesProvider, Clock, QuoteProvider
from domain.quote import QuoteOutcome, QuoteRequest

logger = logging.getLogger(__name__)


class EligibilityGuardProvider:
    def __init__(self, inner: QuoteProvider, rules: AcceptanceRulesProvider, clock: Clock) -> None:
        self._inner = inner
        self._rules = rules
        self._clock = clock

    async def quote(self, req: QuoteRequest) -> QuoteOutcome:
        try:
            rules = await self._rules.current()
        except ConfigurationError:
            raise  # rota ou credencial errada não é catálogo fora do ar (D-035)
        except Exception:
            logger.error("rules_unavailable")
            rules = None
        if rules is not None:
            today = self._clock.today()
            normalized = req.veiculo_ano == today.year + 1
            candidate = replace(req, veiculo_ano=today.year) if normalized else req
            declined = rules.evaluate(candidate, today)
            if declined is not None:
                return replace(declined, origem="regra_local", ano_normalizado=normalized)
        return await self._inner.quote(req)
