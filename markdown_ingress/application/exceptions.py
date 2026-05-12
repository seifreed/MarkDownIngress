"""Exception handling utilities for the application layer."""

from __future__ import annotations

import copy

_SIMPLE_PICKLABLE_TYPES = (str, int, float, bool, bytes, type(None))


def _copy_custom_attrs(source: Exception, target: Exception) -> None:
    """Copy custom instance attributes from source to target exception."""
    skip = {"args", "__cause__", "__suppress_context__", "__notes__", "__traceback__"}
    for attr, value in getattr(source, "__dict__", {}).items():
        if attr not in skip:
            try:
                setattr(target, attr, _make_picklable(value))
            except (AttributeError, TypeError):
                pass


def _copy_batch_exception(exc: Exception) -> Exception:
    """Copy an exception for batch processing, preserving type information.

    Tries multiple fallback strategies to preserve exception type:
    1. Deep copy (works for most exceptions)
    2. Single-arg constructor (works for exceptions like ValueError)
    3. No-arg constructor with args set (works for exceptions with custom init)
    4. RuntimeError fallback (guaranteed to work)

    """
    try:
        return copy.deepcopy(exc)
    except Exception:
        try:
            # Try single-arg constructor (most common case)
            new_exc = type(exc)(str(exc))
            new_exc.__cause__ = exc
            new_exc.__suppress_context__ = getattr(exc, "__suppress_context__", False)
            if hasattr(exc, "__notes__"):
                new_exc.__notes__ = list(exc.__notes__)
            _copy_custom_attrs(exc, new_exc)
            return new_exc
        except Exception:
            try:
                # Try no-arg constructor and set args manually
                new_exc = type(exc)()
                new_exc.args = (str(exc),)
                new_exc.__cause__ = exc
                new_exc.__suppress_context__ = getattr(exc, "__suppress_context__", False)
                if hasattr(exc, "__notes__"):
                    new_exc.__notes__ = list(exc.__notes__)
                _copy_custom_attrs(exc, new_exc)
                return new_exc
            except Exception:
                # Last resort: preserve type name in RuntimeError
                runtime_exc = RuntimeError(f"{type(exc).__name__}: {exc}")
                runtime_exc.__cause__ = exc
                runtime_exc.__suppress_context__ = getattr(exc, "__suppress_context__", False)
                if hasattr(exc, "__notes__"):
                    runtime_exc.__notes__ = list(exc.__notes__)
                _copy_custom_attrs(exc, runtime_exc)
                return runtime_exc


def _make_picklable(value: object) -> object:
    """Convert metadata/exception payloads to conservative Queue-safe values."""
    if isinstance(value, _SIMPLE_PICKLABLE_TYPES):
        return value
    if isinstance(value, dict):
        return {_make_picklable(k): _make_picklable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_make_picklable(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_make_picklable(v) for v in value)
    if isinstance(value, set):
        return [_make_picklable(v) for v in sorted(value, key=repr)]
    return str(value)
