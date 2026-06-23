"""Async task helpers shared across batch execution paths."""

from __future__ import annotations

import asyncio


async def gather_or_cancel[T](tasks: list[asyncio.Task[T]]) -> list[T]:
    """Gather tasks, cancelling every task if the gather itself is cancelled."""
    try:
        return await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
