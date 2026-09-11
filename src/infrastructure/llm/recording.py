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
    LLMToolCall,
    LLMUnavailable,
)
from application.ports import Clock, SystemClock
from infrastructure.llm.budget import budget_expired
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
        clock: Clock | None = None,
    ) -> None:
        if mode not in ("record", "replay") or (mode == "record" and inner is None):
            raise ValueError("Modo de captura LLM inválido")
        self._clock = clock or SystemClock()
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
        canonical = asdict(request)
        if not request.tools:
            canonical.pop("tools")
        # Anexo vazio não altera hashes legados; presente, entra pelo digest dos bytes.
        attachments = canonical.pop("anexos")
        if attachments:
            canonical["anexos"] = [
                {
                    "tipo": item["tipo"],
                    "formato": item["formato"],
                    "sha256": hashlib.sha256(item["dados"]).hexdigest(),
                }
                for item in attachments
            ]
        data = {
            "version": 1,
            "position": position,
            "model": self._models[request.role],
            "request": canonical,
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
            start = self._clock.monotonic()
            try:
                response = await self._inner.complete(request)
            except asyncio.CancelledError:
                # The outer budget cancels this await before translating to unavailable.
                # External cancellation remains an interrupted recording, never a fake failure.
                if budget_expired():
                    latency = (self._clock.monotonic() - start) * 1000
                    self._save(
                        path, {"digest": digest, "error": "unavailable", "latency_ms": latency}
                    )
                    raise LLMUnavailable(latency_ms=latency) from None
                raise
            except (LLMUnavailable, LLMContractError) as error:
                kind = "unavailable" if isinstance(error, LLMUnavailable) else "contract_error"
                error.latency_ms = (self._clock.monotonic() - start) * 1000
                self._save(path, {"digest": digest, "error": kind, "latency_ms": error.latency_ms})
                raise
            safe = replace(
                response,
                content=self._privacy.redact(response.content),
                tool_calls=tuple(
                    LLMToolCall(
                        self._privacy.redact(call.name),
                        self._redact_arguments(call.arguments),
                    )
                    for call in response.tool_calls
                ),
            )
            payload = asdict(safe)
            payload["cost"] = str(safe.cost) if safe.cost is not None else None
            self._save(path, {"digest": digest, "response": payload})
            return safe

    def _redact_arguments(self, arguments: dict[str, object]) -> dict[str, object]:
        return {
            self._privacy.redact(key): self._redact_value(value, field_name=key)
            for key, value in arguments.items()
        }

    def _redact_value(self, value: object, *, field_name: str = "") -> object:
        # Numeric CEPs may have lost a leading zero before reaching this boundary.
        if field_name.casefold() == "cep" and value is not None:
            return "[CEP]"
        if isinstance(value, dict):
            return self._redact_arguments(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, str):
            return self._privacy.redact(value)
        if type(value) in (int, float):
            text = str(value)
            redacted = self._privacy.redact(text)
            return redacted if redacted != text else value
        if value is None or isinstance(value, bool):
            return value
        raise LLMContractError()

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
            failure_latency = data.get("latency_ms")
            if failure_latency is not None:
                failure_latency = float(failure_latency)
                if not math.isfinite(failure_latency) or failure_latency < 0:
                    raise ValueError("latency")
            if data.get("error") == "unavailable":
                raise LLMUnavailable(latency_ms=failure_latency)
            if data.get("error") == "contract_error":
                raise LLMContractError(latency_ms=failure_latency)
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
                self._load_tools(item.get("tool_calls", [])),
            )
        except (KeyError, TypeError, ValueError, ArithmeticError, OSError):
            raise LLMFixtureInvalid() from None

    @staticmethod
    def _load_tools(value: object) -> tuple[LLMToolCall, ...]:
        if not isinstance(value, list):
            raise ValueError("tools")
        calls = []
        for item in value:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("arguments"), dict)
            ):
                raise ValueError("tools")
            calls.append(LLMToolCall(item["name"], item["arguments"]))
        return tuple(calls)
