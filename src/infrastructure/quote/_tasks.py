from __future__ import annotations

import asyncio


async def cancel_and_wait(*tasks: asyncio.Task[object]) -> None:
    """Finaliza também o cancelamento para não deixar chamadas órfãs."""
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
