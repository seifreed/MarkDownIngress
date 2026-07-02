"""Header containers used by public data models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_MISSING = object()


class CaseInsensitiveHeaders(dict[str, str]):
    """Dictionary-like header container with case-insensitive string lookups."""

    @staticmethod
    def _normalize_key(key: str) -> str:
        return key.lower()

    def __init__(self, initial: Mapping[str, str] | None = None, **kwargs: str) -> None:
        super().__init__()
        if initial:
            self.update(initial)
        if kwargs:
            self.update(kwargs)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return super().__contains__(self._normalize_key(key))

    def __getitem__(self, key: str) -> str:
        return super().__getitem__(self._normalize_key(key))

    def __setitem__(self, key: str, value: str) -> None:
        if not isinstance(key, str):
            raise TypeError(f"header key must be a string, got {type(key).__name__}")
        if not isinstance(value, str):
            raise TypeError(f"header value must be a string, got {type(value).__name__}")
        super().__setitem__(self._normalize_key(key), value)

    def __delitem__(self, key: str) -> None:
        super().__delitem__(self._normalize_key(key))

    def get(self, key: object, default: Any = None) -> Any:
        if not isinstance(key, str):
            return default
        return super().get(self._normalize_key(key), default)

    def pop(self, key: object, default: Any = _MISSING) -> Any:
        if not isinstance(key, str):
            if default is _MISSING:
                raise KeyError(key)
            return default
        normalized = self._normalize_key(key)
        if default is _MISSING:
            return super().pop(normalized)
        return super().pop(normalized, default)

    def setdefault(self, key: str, default: str = "") -> str:
        if key in self:
            return self[key]
        self[key] = default
        return default

    def copy(self) -> CaseInsensitiveHeaders:
        return type(self)(self)

    def update(
        self,
        *args: Any,
        **kwargs: str,
    ) -> None:
        if len(args) > 1:
            raise TypeError(f"update expected at most 1 argument, got {len(args)}")
        other = args[0] if args else None
        if other:
            items = other.items() if isinstance(other, Mapping) else other
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value
