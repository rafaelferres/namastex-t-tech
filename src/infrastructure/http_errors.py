"""Classificação e descrição de respostas de falha, comuns a todo cliente externo."""

from __future__ import annotations

import httpx

from infrastructure.privacy import PrivacyRedactor

# Credencial, rota, método ou parâmetro rejeitado: configuração nossa, não o serviço.
CONFIGURATION = frozenset({400, 401, 402, 403, 404, 405, 413, 422})
_TRANSIENT_4XX = frozenset({408, 425, 429})
_LIMIT = 500
_REDACTOR = PrivacyRedactor()


def is_transient(status: int) -> bool:
    return status in _TRANSIENT_4XX or 500 <= status < 600


def is_configuration(status: int) -> bool:
    # Redirecionamento sem follow_redirects também é URL base errada.
    return status in CONFIGURATION or 300 <= status < 400


def describe(response: httpx.Response) -> str:
    """Status e corpo redigido e truncado; é o que vai para log e trace."""
    try:
        text = response.text
    except Exception:
        text = "<corpo ilegível>"
    body = _REDACTOR.redact(" ".join(text.split()))
    return f"HTTP {response.status_code}: {body[:_LIMIT]}"


def describe_transport(error: BaseException) -> str:
    return f"transporte: {type(error).__name__}"
