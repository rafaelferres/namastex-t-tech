from __future__ import annotations

import asyncio
from collections.abc import Callable


async def run_sqlite[T](operation: Callable[[], T]) -> T:
    """Cancelamento não pode abandonar um worker que ainda usa a conexão."""
    worker = asyncio.create_task(asyncio.to_thread(operation))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                raise
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result
