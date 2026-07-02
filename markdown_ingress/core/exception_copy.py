"""Queue-safe exception copying helpers shared across ingestion paths."""

from __future__ import annotations

import copy
from contextlib import suppress

from markdown_ingress.models import SafeDocument

_SIMPLE_PICKLABLE_TYPES = (str, int, float, bool, bytes, type(None))


def make_picklable(value: object) -> object:
    """Convert metadata/exception payloads to conservative Queue-safe values."""
    if isinstance(value, _SIMPLE_PICKLABLE_TYPES):
        return value
    if isinstance(value, dict):
        return {make_picklable(k): make_picklable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [make_picklable(v) for v in value]
    if isinstance(value, tuple):
        return tuple(make_picklable(v) for v in value)
    if isinstance(value, set):
        return [make_picklable(v) for v in sorted(value, key=repr)]
    return str(value)


def _copy_custom_attrs(source: Exception, target: Exception) -> None:
    """Copy custom instance attributes from source to target exception."""
    skip = {
        "args",
        "__cause__",
        "__context__",
        "__suppress_context__",
        "__notes__",
        "__traceback__",
    }
    for attr, value in getattr(source, "__dict__", {}).items():
        if attr not in skip:
            with suppress(AttributeError, TypeError):
                copied_value: object
                if attr == "document":
                    copied_value = copy.deepcopy(value) if isinstance(value, SafeDocument) else None
                else:
                    copied_value = make_picklable(value)
                setattr(target, attr, copied_value)


def _prepare_exception_copy(
    source: Exception,
    target: Exception,
    *,
    args: tuple[object, ...] | None = None,
) -> Exception:
    """Apply queue-safe state from source to a copied exception."""
    raw_args = source.args if args is None else args
    with suppress(AttributeError, TypeError):
        target.args = tuple(make_picklable(arg) for arg in raw_args)
    # Queue-safe by construction: __cause__/__context__/__traceback__ are
    # excluded from Python's default exception serialization (only type, args
    # and __dict__ are carried). Attaching the raw source preserves the real
    # debug chain on the in-process (inflight thread) path, and is silently
    # dropped — not serialized — when the copy crosses the subprocess
    # multiprocessing.Queue, so it can never reintroduce unserializable state.
    target.__cause__ = source
    target.__suppress_context__ = getattr(source, "__suppress_context__", False)
    if hasattr(source, "__notes__"):
        target.__notes__ = list(source.__notes__)
    _copy_custom_attrs(source, target)
    return target


def copy_exception_for_transfer(exc: Exception) -> Exception:
    """Copy an exception for cross-task/process transfer, preserving useful debug state."""
    try:
        return _prepare_exception_copy(exc, copy.deepcopy(exc))
    except Exception:  # noqa: BLE001 - exception copying falls back by design
        try:
            new_exc = type(exc)(str(exc))
            return _prepare_exception_copy(exc, new_exc)
        except Exception:  # noqa: BLE001 - exception copying falls back by design
            try:
                new_exc = type(exc)()
                fallback_args = exc.args or (str(exc),)
                return _prepare_exception_copy(exc, new_exc, args=fallback_args)
            except Exception:  # noqa: BLE001 - exception copying falls back by design
                runtime_exc = RuntimeError(f"{type(exc).__name__}: {exc}")
                return _prepare_exception_copy(exc, runtime_exc, args=runtime_exc.args)
