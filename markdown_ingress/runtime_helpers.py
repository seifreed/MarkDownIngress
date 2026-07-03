"""Shared runtime helpers used by sync wrappers."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from markdown_ingress.config_validation import validate_positive_int

UNSET = object()
_INGEST_MANY_IN_LOOP_ERROR = (
    "ingest_many() cannot run inside an active event loop; use ingest_many_async() instead"
)


def run_ingest_many_blocking[T](coro_factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run an ingest_many coroutine to completion from synchronous context."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    raise RuntimeError(_INGEST_MANY_IN_LOOP_ERROR)


def validate_batch_max_concurrent(value: object) -> int:
    """Validate and coerce `max_concurrent`."""
    return validate_positive_int("max_concurrent", value)
