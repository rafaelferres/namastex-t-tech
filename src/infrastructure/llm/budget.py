"""Limite por conversa compartilhado entre papéis, independente do transporte."""

from __future__ import annotations

import asyncio

from application.llm import LLMClient, LLMRequest, LLMResponse, LLMUnavailable, TokenBudgetExceeded


class BudgetedLLMClient:
    def __init__(self, inner: LLMClient, token_limit: int = 4000) -> None:
        if token_limit <= 0:
            raise ValueError("Limite de tokens inválido")
        self._inner = inner
        self._token_limit = token_limit
        self._used: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def tokens_used(self, conversation_id: str) -> int:
        return self._used.get(conversation_id, 0)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        conversation_id = request.conversation_id
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        try:
            async with asyncio.timeout(request.budget):
                async with lock:
                    if self.tokens_used(conversation_id) >= self._token_limit:
                        raise TokenBudgetExceeded()
                    response = await self._inner.complete(request)
                    used = (
                        self.tokens_used(conversation_id)
                        + response.prompt_tokens
                        + response.completion_tokens
                    )
                    self._used[conversation_id] = used
                    if used > self._token_limit:
                        raise TokenBudgetExceeded()
                    return response
        except TimeoutError:
            raise LLMUnavailable() from None
