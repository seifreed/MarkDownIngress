"""
Utility helpers for cache backend identity and TTL validation.
"""

from pathlib import Path
from typing import Any

_MAX_CACHE_TTL_SECONDS = 31_536_000  # 365 days


def _normalize_identity_value(value: Any) -> Any:
    """Convert backend attributes into JSON-friendly identity values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _normalize_identity_value(subvalue)
            for key, subvalue in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_identity_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_normalize_identity_value(item) for item in value), key=str)
    return repr(value)


def _is_stable_identity_value(value: Any) -> bool:
    """Return whether a value is safe to include in a backend fingerprint."""
    if value is None or isinstance(value, (bool, int, float, str, Path)):
        return True
    if isinstance(value, dict):
        return all(
            _is_stable_identity_value(key) and _is_stable_identity_value(subvalue)
            for key, subvalue in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return all(_is_stable_identity_value(item) for item in value)
    return False


def _collect_public_identity_attrs(cache_backend: object) -> dict[str, Any]:
    """Collect stable public attributes from a cache backend instance.

    This covers both normal ``__dict__``-based objects and slot-based objects
    so custom cache backends do not collapse to a type-only fingerprint.
    """
    attrs: dict[str, Any] = {}

    try:
        for name, value in vars(cache_backend).items():
            if (
                not name.startswith("_")
                and not callable(value)
                and _is_stable_identity_value(value)
            ):
                attrs[name] = _normalize_identity_value(value)
    except TypeError:
        pass

    for cls in type(cache_backend).__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if slot in {"__dict__", "__weakref__"} or slot.startswith("_"):
                continue
            if slot in attrs:
                continue
            try:
                value = getattr(cache_backend, slot)
            except AttributeError:
                continue
            if callable(value):
                continue
            attrs[slot] = _normalize_identity_value(value)

    # Private slot values are only included when they are simple, stable data.
    # This captures semantically meaningful slot-only backends while avoiding
    # runtime objects such as locks, sockets, or database connections.
    for cls in type(cache_backend).__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if not slot.startswith("_") or slot in {"__dict__", "__weakref__"}:
                continue
            if slot in attrs:
                continue
            try:
                value = getattr(cache_backend, slot)
            except AttributeError:
                continue
            if callable(value) or not _is_stable_identity_value(value):
                continue
            attrs[slot] = _normalize_identity_value(value)

    return {key: attrs[key] for key in sorted(attrs)}


def _validate_ttl_value(ttl: int, *, field_name: str) -> int:
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise ValueError(f"{field_name} must be an int, got {type(ttl).__name__}")
    if ttl <= 0:
        raise ValueError(
            f"{field_name} must be positive, got {ttl}. Permanent entries (TTL=0) are not supported to prevent unbounded growth."
        )
    if ttl > _MAX_CACHE_TTL_SECONDS:
        raise ValueError(
            f"{field_name} exceeds the maximum supported TTL of {_MAX_CACHE_TTL_SECONDS} seconds, got {ttl}."
        )
    return ttl
