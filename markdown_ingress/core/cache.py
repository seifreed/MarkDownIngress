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

    @abstractmethod
    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """Store document in cache"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete document from cache"""

    @abstractmethod
    def clear(self) -> None:
        """Clear entire cache"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists in cache"""

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
