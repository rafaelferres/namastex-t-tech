"""Capturas imutáveis: reprodução nunca faz fallback para rede."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from application.llm import (
    LLMClient,
    LLMContractError,
    LLMRequest,
    LLMResponse,
    LLMRole,
    LLMUnavailable,
)
from infrastructure.privacy import PrivacyRedactor


class LLMFixtureMissing(LLMContractError):
    def __init__(self, digest: str) -> None:
        Exception.__init__(self, f"Fixture LLM ausente: {digest}")


class LLMFixtureInvalid(LLMContractError):
    def __init__(self) -> None:
        Exception.__init__(self, "Fixture LLM inválida")


class RecordedLLMClient:
    def __init__(
        self,
        inner: LLMClient | None,
        directory: Path,
        mode: Literal["record", "replay"],
        models: Mapping[LLMRole, str],
        *,
        settings: Mapping[str, object] | None = None,
    ) -> None:
        if mode not in ("record", "replay") or (mode == "record" and inner is None):
            raise ValueError("Modo de captura LLM inválido")
        self._inner = inner
        self._directory = directory
        self._mode = mode
        self._models = dict(models)
        self._settings = dict(settings or {})
        self._positions: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._privacy = PrivacyRedactor()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        position = self._positions.get(request.conversation_id, 0)
        self._positions[request.conversation_id] = position + 1
        data = {
            "version": 1,
            "position": position,
            "model": self._models[request.role],
            "request": asdict(request),
            "settings": self._settings,
        }
        digest = hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        path = self._directory / f"{digest}.json"
        async with self._locks.setdefault(digest, asyncio.Lock()):
            if path.is_file():
                return self._load(path, digest)
            if self._mode == "replay":
                raise LLMFixtureMissing(digest)
            if self._inner is None:
                raise LLMContractError()
            try:
                response = await self._inner.complete(request)
            except (LLMUnavailable, LLMContractError) as error:
                kind = "unavailable" if isinstance(error, LLMUnavailable) else "contract_error"
                self._save(path, {"digest": digest, "error": kind})
                raise
            safe = replace(response, content=self._privacy.redact(response.content))
            payload = asdict(safe)
            payload["cost"] = str(safe.cost) if safe.cost is not None else None
            self._save(path, {"digest": digest, "response": payload})
            return safe

    def _save(self, path: Path, payload: dict[str, object]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", dir=self._directory, suffix=".tmp", encoding="utf-8", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _load(self, path: Path, digest: str) -> LLMResponse:
        try:
            data = json.loads(path.read_text())
            if data["digest"] != digest:
                raise ValueError("digest")
            if data.get("error") == "unavailable":
                raise LLMUnavailable()
            if data.get("error") == "contract_error":
                raise LLMContractError()
            item = data["response"]
            if not isinstance(item["content"], str) or not isinstance(item["model"], str):
                raise ValueError("text")
            for key in ("prompt_tokens", "completion_tokens"):
                if type(item[key]) is not int or item[key] < 0:
                    raise ValueError("tokens")
            cost = Decimal(item["cost"]) if item["cost"] is not None else None
            latency = float(item["latency_ms"])
            if (
                not math.isfinite(latency)
                or latency < 0
                or (cost is not None and (not cost.is_finite() or cost < 0))
            ):
                raise ValueError("usage")
            return LLMResponse(
                item["content"],
                item["model"],
                item["prompt_tokens"],
                item["completion_tokens"],
                cost,
                latency,
            )
        except (KeyError, TypeError, ValueError, ArithmeticError, OSError):
            raise LLMFixtureInvalid() from None
