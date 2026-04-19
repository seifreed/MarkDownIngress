"""
Caching layer for processed documents.

TTL Behavior:
    - TTL > 0: Entry expires after specified seconds (required)
    - TTL <= 0: ValueError raised (permanent entries not supported)

Note: TTL=0 (permanent entries) is not supported to prevent unbounded memory/disk
growth in long-running applications. Use a sufficiently large TTL instead.

Concrete implementations (MemoryCache, SQLiteCache) live in
``markdown_ingress.adapters.cache``.
"""

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any

from markdown_ingress.core.ssrf import normalize_url_for_identity
from markdown_ingress.models import SafeDocument


class Cache(ABC):  # implements ICacheBackend protocol
    """Abstract cache interface"""

    @abstractmethod
    def get(self, key: str) -> SafeDocument | None:
        """Get document from cache"""
        pass  # pragma: no cover

    @abstractmethod
    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """Store document in cache"""
        pass  # pragma: no cover

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete document from cache"""
        pass  # pragma: no cover

    @abstractmethod
    def clear(self) -> None:
        """Clear entire cache"""
        pass  # pragma: no cover

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        pass  # pragma: no cover

    @staticmethod
    def make_key(
        url: str,
        mode: str = "fast",
        strict: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate cache key from URL and effective request parameters.

        Args:
            url: Source URL
            mode: Fetching mode
            strict: Strict mode flag
            extra: Optional JSON-serializable request dimensions (from
                   build_request_identity, which already covers all config
                   fields that affect output)

        Returns:
            Cache key string
        """
        key_payload: dict[str, Any] = {
            "url": normalize_url_for_identity(url),
            "mode": mode,
            "strict": strict,
        }
        if extra:
            key_payload["extra"] = extra
        key_data = json.dumps(key_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(key_data.encode()).hexdigest()


def cache_backend_identity(cache_backend: object | None) -> dict[str, Any] | None:
    """Return a stable JSON-serializable fingerprint for a cache backend."""
    if cache_backend is None:
        return None

    while hasattr(cache_backend, "__wrapped__"):
        cache_backend = getattr(cache_backend, "__wrapped__")

    identity: dict[str, Any] = {
        "type": f"{cache_backend.__class__.__module__}.{cache_backend.__class__.__qualname__}",
    }
    for attr in ("default_ttl", "max_entries", "db_path", "cleanup_threshold"):
        if hasattr(cache_backend, attr):
            identity[attr] = getattr(cache_backend, attr)

    return identity
