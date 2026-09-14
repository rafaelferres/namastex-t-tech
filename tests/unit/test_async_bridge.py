"""Ponte única entre o Streamlit, síncrono, e a pilha async (tarefa 14)."""

from __future__ import annotations

import asyncio

import pytest

from interfaces.async_bridge import AsyncBridge


def test_every_call_runs_on_the_same_persistent_loop() -> None:
    bridge = AsyncBridge()
    try:
        first, second = bridge.run(_loop()), bridge.run(_loop())
        assert first is second
        assert first.is_running()
    finally:
        bridge.close()


def test_state_created_on_the_loop_survives_between_calls() -> None:
    # A pilha (clientes httpx, conexões, tarefas do ingestor) vive entre reexecuções do script.
    bridge = AsyncBridge()
    try:
        event = bridge.run(_make_event())
        bridge.run(_set(event))
        assert bridge.run(_wait(event)) is True
    finally:
        bridge.close()


def test_exception_propagates_to_the_caller() -> None:
    bridge = AsyncBridge()
    try:
        with pytest.raises(ValueError, match="falhou no loop"):
            bridge.run(_fail())
    finally:
        bridge.close()


def test_close_stops_the_loop() -> None:
    bridge = AsyncBridge()
    loop = bridge.run(_loop())
    bridge.close()
    assert not loop.is_running()


async def _loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


async def _make_event() -> asyncio.Event:
    return asyncio.Event()


async def _set(event: asyncio.Event) -> None:
    event.set()


async def _wait(event: asyncio.Event) -> bool:
    return await asyncio.wait_for(event.wait(), timeout=1)


async def _fail() -> None:
    raise ValueError("falhou no loop")
