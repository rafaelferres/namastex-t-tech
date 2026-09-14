"""Ponte única entre código síncrono (Streamlit) e a pilha async (tarefa 14).

Streamlit reexecuta o script a cada interação; a pilha — clientes httpx, conexões SQLite,
tarefas do ingestor — precisa de um loop que sobreviva a isso. Um loop numa thread, e toda
chamada async passa por `run`.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any


class AsyncBridge:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="async-bridge", daemon=True
        )
        self._thread.start()

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()
