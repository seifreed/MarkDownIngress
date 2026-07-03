"""Shared runtime helpers used by sync wrappers."""

from __future__ import annotations

import sys
from collections.abc import Callable, Coroutine
from functools import lru_cache
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from markdown_ingress.config_validation import validate_positive_int

UNSET = object()
_INGEST_MANY_IN_LOOP_ERROR = (
    "ingest_many() cannot run inside an active event loop; use ingest_many_async() instead"
)
_DEFAULT_FIND_SPEC = find_spec


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


@lru_cache(maxsize=128)
def _is_dependency_available_cached(
    module_name: str,
    find_spec_fn: Callable[[str], object | None],
) -> bool:
    """Check dependency availability by module name and lookup strategy."""
    return find_spec_fn(module_name) is not None


def is_dependency_available(module_name: str) -> bool:
    """Check if an optional dependency can be imported by module name."""
    is_find_spec_patched = find_spec is not _DEFAULT_FIND_SPEC
    if module_name in sys.modules and not is_find_spec_patched:
        return True
    return _is_dependency_available_cached(
        module_name,
        find_spec,
    )


setattr(is_dependency_available, "cache_clear", _is_dependency_available_cached.cache_clear)


def load_optional_module(
    module_name: str,
    *,
    pip_name: str | None = None,
    purpose: str | None = None,
) -> Any:
    """Import an optional dependency with a clear error when missing."""
    if not is_dependency_available(module_name):
        package = pip_name or module_name.split(".")[0]
        feature = f" for {purpose}" if purpose else ""
        raise ImportError(
            f"Optional dependency{feature} '{module_name}' is not installed. "
            f"Install with: pip install {package}."
        )
    return import_module(module_name)


def load_optional_object(
    module_name: str,
    object_name: str,
    *,
    pip_name: str | None = None,
    purpose: str | None = None,
) -> Any:
    """Return ``getattr(imported_module, object_name)`` with a clean ImportError."""
    module = load_optional_module(module_name, pip_name=pip_name, purpose=purpose)
    try:
        return getattr(module, object_name)
    except AttributeError as exc:
        package = pip_name or module_name.split(".")[0]
        feature = f" for {purpose}" if purpose else ""
        raise ImportError(
            f"Optional dependency{feature} '{module_name}' does not export {object_name!r}. "
            f"Install/update {package}."
        ) from exc
