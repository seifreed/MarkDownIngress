"""
In-memory LRU cache implementation.
"""

import copy
import logging
import threading
import time
from typing import Any

from markdown_ingress.adapters.cache.utils import _validate_ttl_value
from markdown_ingress.core.cache import Cache
from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)


class MemoryCache(Cache):  # implements ICacheBackend protocol
    """In-memory cache implementation with LRU eviction.

    TTL Behavior:
        - TTL > 0: Entry expires after specified seconds (required)
        - TTL <= 0: ValueError raised (permanent entries not supported)

    Thread Safety:
        All operations are thread-safe. The get() method returns a copy of
        the document to prevent TOCTOU (Time-of-check to time-of-use) issues
        where the caller might modify the cached document reference.
    """

    def __init__(self, default_ttl: int = 3600, max_entries: int = 10000):
        """
        Initialize memory cache.

        Args:
            default_ttl: Default time-to-live in seconds. Must be positive (> 0).
            max_entries: Maximum number of entries before LRU eviction (0 = unlimited)

        Raises:
            ValueError: If default_ttl is not positive
        """
        self.default_ttl = _validate_ttl_value(default_ttl, field_name="default_ttl")
        if max_entries < 0:
            raise ValueError(f"max_entries must be >= 0, got {max_entries}")
        self.max_entries = max_entries
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> SafeDocument | None:
        """Get document from cache if not expired, updating LRU access time.

        Returns a copy of the document to prevent TOCTOU issues where the caller
        might modify the cached document reference. This ensures thread-safety
        even when the returned document is modified by the caller.

        BUG FIX: Performs deepcopy outside the lock to avoid blocking other threads
        during expensive copy operations on large documents.

        Returns:
            A copy of the SafeDocument if found and not expired, None otherwise.
        """
        # BUG FIX: Shallow copy of entry dict to hold lock for minimal time
        entry_copy: dict[str, Any] | None = None
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            # Check expiration
            if time.time() >= entry["expires_at"]:
                self._cache.pop(key, None)
                return None

            # Update last access time for LRU
            entry["last_access"] = time.time()

            # BUG FIX: Shallow copy the entry dict while holding lock
            # Then do expensive deepcopy outside lock
            entry_copy = entry.copy()

        if entry_copy is None:
            return None

        # Perform expensive deepcopy outside the lock
        doc = entry_copy["document"]
        if isinstance(doc, SafeDocument):
            return copy.deepcopy(doc)
        _logger.warning("Cache returned non-SafeDocument object of type %s", type(doc).__name__)
        return None

    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """Store document in cache with LRU eviction if max_entries exceeded.

        Args:
            key: Cache key
            document: SafeDocument to store
            ttl: Time-to-live in seconds. If None, uses default_ttl.
                Must be a positive value (> 0).

        Raises:
            ValueError: If TTL is negative or zero

        Note:
            TTL=0 (permanent entries) is no longer supported to prevent
            unbounded memory growth. Use a sufficiently large TTL instead.
        """
        ttl = _validate_ttl_value(ttl if ttl is not None else self.default_ttl, field_name="TTL")
        expires_at = time.time() + ttl

        with self._lock:
            # Evict BEFORE insert to keep cache within max_entries limit.
            # >= is correct: evict first so the cache never holds more than max_entries items.
            if (
                self.max_entries > 0
                and len(self._cache) >= self.max_entries
                and key not in self._cache
            ):
                self._evict_lru_locked()

            self._cache[key] = {
                "document": copy.deepcopy(document),
                "expires_at": expires_at,
                "created_at": time.time(),
                "last_access": time.time(),
            }

    def _evict_lru_locked(self) -> None:
        """Evict least recently used entries. Must be called with lock held."""
        if not self._cache:
            return

        # Find entries that are expired first (they should be evicted regardless of LRU)
        now = time.time()
        expired_keys = [key for key, entry in self._cache.items() if now >= entry["expires_at"]]

        for key in expired_keys:
            self._cache.pop(key, None)

        if self.max_entries == 0:
            return

        # If still over limit, evict by LRU (oldest last_access)
        if len(self._cache) >= self.max_entries:
            # Sort by last_access and remove oldest entries
            items = sorted(
                self._cache.items(),
                key=lambda kv: kv[1].get("last_access", kv[1].get("created_at", 0)),
            )
            # Runtime fix (L8): at the exact max_entries boundary, evict only
            # the single LRU entry. The previous "10% or at least 1" rule
            # could prune up to 10 entries when exceeding the limit by just
            # one insert, surprising callers that relied on a steady cache.
            # Evict exactly the minimum needed (1 slot for the incoming insert).
            overshoot = len(self._cache) - self.max_entries + 1
            to_remove = max(1, overshoot)
            for key, _ in items[:to_remove]:
                self._cache.pop(key, None)

    def delete(self, key: str) -> None:
        """Delete entry from cache"""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired without copying.

        BUG FIX: Previously called get() which performed expensive deepcopy.
        This implementation checks existence directly without copying.

        NOTE: Does NOT update last_access to avoid TOCTOU issues. If you need
        to update LRU order, call get() instead. This prevents exists() from
        changing cache ordering which could cause subsequent get() calls to
        return unexpected results on nearly-full caches.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            # Check expiration without copying; evict expired entry to prevent LRU inflation
            if time.time() >= entry["expires_at"]:
                self._cache.pop(key, None)
                return False
            return True

    def cleanup_expired(self) -> int:
        """
        Remove expired entries from cache.

        Returns:
            Number of entries removed
        """
        with self._lock:
            now = time.time()
            expired_keys = [key for key, entry in self._cache.items() if now >= entry["expires_at"]]

            for key in expired_keys:
                self._cache.pop(key, None)

            return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        with self._lock:
            return {
                "size": len(self._cache),
                "max_entries": self.max_entries,
                "default_ttl": self.default_ttl,
            }
