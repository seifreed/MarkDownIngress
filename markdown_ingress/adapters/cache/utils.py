"""
Utility helpers for cache backend identity and TTL validation.
"""

from collections.abc import Iterator
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
    attrs = _collect_dict_identity_attrs(cache_backend)
    _collect_slot_identity_attrs(cache_backend, attrs, include_private=False)
    _collect_slot_identity_attrs(cache_backend, attrs, include_private=True)
    return {key: attrs[key] for key in sorted(attrs)}


def _collect_dict_identity_attrs(cache_backend: object) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    try:
        for name, value in vars(cache_backend).items():
            if _is_public_dict_identity_attr(name, value):
                attrs[name] = _normalize_identity_value(value)
    except TypeError:
        pass
    return attrs


def _is_public_dict_identity_attr(name: str, value: Any) -> bool:
    return not name.startswith("_") and not callable(value) and _is_stable_identity_value(value)


def _collect_slot_identity_attrs(
    cache_backend: object,
    attrs: dict[str, Any],
    *,
    include_private: bool,
) -> None:
    for slot in _iter_declared_slots(cache_backend):
        if _skip_slot(slot, attrs, include_private=include_private):
            continue
        try:
            value = getattr(cache_backend, slot)
        except AttributeError:
            continue
        if _slot_value_is_collectable(value, include_private=include_private):
            attrs[slot] = _normalize_identity_value(value)


def _iter_declared_slots(cache_backend: object) -> Iterator[str]:
    for cls in type(cache_backend).__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            yield slots
            continue
        yield from slots


def _skip_slot(slot: str, attrs: dict[str, Any], *, include_private: bool) -> bool:
    if slot in {"__dict__", "__weakref__"} or slot in attrs:
        return True
    if include_private:
        return not slot.startswith("_")
    return slot.startswith("_")


def _slot_value_is_collectable(value: Any, *, include_private: bool) -> bool:
    if callable(value):
        return False
    return not include_private or _is_stable_identity_value(value)


def _validate_ttl_value(ttl: int, *, field_name: str) -> int:
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise ValueError(f"{field_name} must be an int, got {type(ttl).__name__}")
    if ttl <= 0:
        raise ValueError(
            f"{field_name} must be positive, got {ttl}. Permanent entries "
            "(TTL=0) are not supported to prevent unbounded growth."
        )
    if ttl > _MAX_CACHE_TTL_SECONDS:
        raise ValueError(
            f"{field_name} exceeds the maximum supported TTL of "
            f"{_MAX_CACHE_TTL_SECONDS} seconds, got {ttl}."
        )
    return ttl
