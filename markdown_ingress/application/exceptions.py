"""Exception handling utilities for the application layer."""
from __future__ import annotations

import copy
import pickle


def _copy_custom_attrs(source: Exception, target: Exception) -> None:
    """Copy custom instance attributes from source to target exception."""
    skip = {"args", "__cause__", "__suppress_context__", "__notes__", "__traceback__"}
    for attr, value in getattr(source, "__dict__", {}).items():
        if attr not in skip:
            try:
                setattr(target, attr, value)
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


def _is_picklable(obj: object) -> bool:
    """Check if an object can be pickled."""
    try:
        pickle.dumps(obj)
        return True
    except Exception:
        return False


def _make_picklable(value: object) -> object:
    """Make a value picklable by converting non-picklable leaves to strings."""
    if isinstance(value, dict):
        return {k: _make_picklable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_make_picklable(v) for v in value]
    if not _is_picklable(value):
        return str(value)
    return value
