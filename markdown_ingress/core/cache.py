"""
Caching layer for processed documents.

TTL Behavior:
    - TTL > 0: Entry expires after specified seconds (required)
    - TTL <= 0: ValueError raised (permanent entries not supported)

Note: TTL=0 (permanent entries) is not supported to prevent unbounded memory/disk
growth in long-running applications. Use a sufficiently large TTL instead.
"""

import copy
import hashlib
import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)
_MAX_CACHE_TTL_SECONDS = 31_536_000  # 365 days


def _validate_ttl_value(ttl: int, *, field_name: str) -> int:
    if ttl <= 0:
        raise ValueError(
            f"{field_name} must be positive, got {ttl}. Permanent entries (TTL=0) are not supported to prevent unbounded growth."
        )
    if ttl > _MAX_CACHE_TTL_SECONDS:
        raise ValueError(
            f"{field_name} exceeds the maximum supported TTL of {_MAX_CACHE_TTL_SECONDS} seconds, got {ttl}."
        )
    return ttl

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
        user_agent: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate cache key from URL and effective request parameters.

        Args:
            url: Source URL
            mode: Fetching mode
            strict: Strict mode flag
            user_agent: User-Agent string (included in key to prevent wrong cached content)
            extra: Optional JSON-serializable request dimensions

        Returns:
            Cache key string

        Note:
            Including user_agent ensures that different User-Agents get different
            cached content, preventing issues where a mobile UA response is cached
            and served to desktop clients, etc.
        """
        key_payload: dict[str, Any] = {"url": url, "mode": mode, "strict": strict}
        if user_agent:
            key_payload["ua_sha256"] = hashlib.sha256(user_agent.encode()).hexdigest()
        if extra:
            key_payload["extra"] = extra
        key_data = json.dumps(key_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(key_data.encode()).hexdigest()


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
        self.max_entries = max(0, max_entries)  # Ensure non-negative
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> SafeDocument | None:
        """Get document from cache if not expired, updating LRU access time.

        Returns a copy of the document to prevent TOCTOU issues where the caller
        might modify the cached document reference. This ensures thread-safety
        even when the returned document is modified by the caller.

        Note: The entire get operation (check, expiration, copy) is performed
        within the lock to prevent TOCTOU issues where an entry could expire
        between the existence check and the copy operation.

        Returns:
            A copy of the SafeDocument if found and not expired, None otherwise.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            # Check expiration
            if entry["expires_at"] > 0 and time.time() >= entry["expires_at"]:
                self._cache.pop(key, None)
                return None

            # Update last access time for LRU
            entry["last_access"] = time.time()

            # Return a copy to prevent TOCTOU issues - done within lock to ensure
            # the entry is not modified/expired between check and copy
            doc = entry["document"]
            if isinstance(doc, SafeDocument):
                # Use deepcopy to ensure nested objects are also copied
                return copy.deepcopy(doc)
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
            # Periodically drop expired entries and enforce capacity when bounded.
            cleanup_threshold = 100 if self.max_entries == 0 else max(100, self.max_entries // 10)
            if len(self._cache) > cleanup_threshold or (
                self.max_entries > 0 and len(self._cache) >= self.max_entries
            ):
                self._evict_lru_locked()

            self._cache[key] = {
                "document": copy.deepcopy(document),
                "expires_at": expires_at,
                "created_at": time.time(),
                "last_access": time.time(),
            }
            if self.max_entries > 0 and len(self._cache) > self.max_entries:
                self._evict_lru_locked()

    def _evict_lru_locked(self) -> None:
        """Evict least recently used entries. Must be called with lock held."""
        if not self._cache:
            return

        # Find entries that are expired first (they should be evicted regardless of LRU)
        now = time.time()
        expired_keys = [
            key for key, entry in self._cache.items()
            if entry["expires_at"] > 0 and now >= entry["expires_at"]
        ]

        for key in expired_keys:
            self._cache.pop(key, None)

        if self.max_entries == 0:
            return

        # If still over limit, evict by LRU (oldest last_access)
        if len(self._cache) >= self.max_entries:
            # Sort by last_access and remove oldest entries
            items = sorted(
                self._cache.items(),
                key=lambda kv: kv[1].get("last_access", kv[1].get("created_at", 0))
            )
            # Remove oldest 10% or at least 1 entry
            to_remove = max(1, len(items) // 10)
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
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            # Check expiration without copying
            if entry["expires_at"] > 0 and time.time() >= entry["expires_at"]:
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
            expired_keys = [
                key
                for key, entry in self._cache.items()
                if entry["expires_at"] > 0 and now >= entry["expires_at"]
            ]

            for key in expired_keys:
                self._cache.pop(key, None)

            return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        return {
            "size": len(self._cache),
            "max_entries": self.max_entries,
            "default_ttl": self.default_ttl,
        }


class SQLiteCache(Cache):  # implements ICacheBackend protocol
    """SQLite-based persistent cache with automatic cleanup.

    TTL Behavior:
        - TTL > 0: Entry expires after specified seconds (required)
        - TTL <= 0: ValueError raised (permanent entries not supported)

    Thread Safety:
        All operations are thread-safe. Uses a lock for database access.

    Connection Management:
        - Use close() method to explicitly close the connection
        - Use as context manager (with statement) for automatic cleanup
        - The __del__ method provides fallback cleanup, but explicit close() is preferred

    Cleanup Strategy:
        - Expired entries are cleaned up during get() operations
        - Periodic cleanup is triggered during set() operations when the
          entry count exceeds the cleanup threshold (default: 1000 entries)
    """

    # Threshold for triggering periodic cleanup during set() operations
    DEFAULT_CLEANUP_THRESHOLD = 1000

    @staticmethod
    def _validate_db_path(db_path: str) -> Path:
        """Validate and resolve the database path.

        Args:
            db_path: Path to SQLite database file

        Returns:
            Resolved Path object

        Raises:
            ValueError: If path is empty, or attempts path traversal outside
                the current working directory
        """
        if not db_path or not db_path.strip():
            raise ValueError("db_path cannot be empty")

        # Convert to Path and resolve to absolute path
        path = Path(db_path)

        # Resolve the path to get the canonical form (resolves .., ., symlinks)
        try:
            # For paths that don't exist yet, resolve parent first
            if path.exists():
                resolved_path = path.resolve()
            else:
                # Resolve parent directory and then add the filename
                parent = path.parent
                if parent.exists():
                    resolved_parent = parent.resolve()
                else:
                    # Create parent path by resolving from cwd
                    resolved_parent = Path.cwd() / parent
                    resolved_parent = resolved_parent.resolve()
                resolved_path = resolved_parent / path.name
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid db_path '{db_path}': {exc}") from exc

        # Get current working directory as the base allowed directory
        cwd = Path.cwd().resolve()

        # Check that the resolved path is within the current working directory
        # or a subdirectory of it
        try:
            resolved_path.relative_to(cwd)
        except ValueError:
            # Path is outside cwd - check if it's an absolute path that was explicitly given
            if path.is_absolute():
                # Allow absolute paths but log a warning
                _logger.warning(
                    "SQLiteCache using absolute path '%s' outside working directory '%s'. "
                    "Ensure this path is intentionally specified and secure.",
                    resolved_path,
                    cwd,
                )
            else:
                raise ValueError(
                    f"db_path '{db_path}' resolves to path outside current working directory. "
                    f"Resolved path: '{resolved_path}', Working directory: '{cwd}'. "
                    "Path traversal is not allowed for security reasons."
                ) from None

        return resolved_path

    def __init__(
        self,
        db_path: str = ".cache/markdowningress.db",
        default_ttl: int = 3600,
        cleanup_threshold: int = DEFAULT_CLEANUP_THRESHOLD,
    ):
        """
        Initialize SQLite cache.

        Args:
            db_path: Path to SQLite database file. Must be a non-empty string.
                Path traversal (e.g., '../../etc/passwd') is not allowed.
                Relative paths are resolved from the current working directory.
                Absolute paths are allowed but will trigger a warning.
            default_ttl: Default TTL in seconds. Must be positive (> 0).
            cleanup_threshold: Number of entries that triggers automatic cleanup
                during set() operations. Set to 0 to disable periodic cleanup.

        Raises:
            ValueError: If default_ttl is not positive, or if db_path is empty/invalid
        """
        import sqlite3

        # Validate and resolve the database path
        self.db_path = self._validate_db_path(db_path)
        self.default_ttl = _validate_ttl_value(default_ttl, field_name="default_ttl")
        self.cleanup_threshold = cleanup_threshold
        self._closed = False

        # Create directory if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database with proper cleanup on failure
        self._db_lock = threading.Lock()
        try:
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._init_db()
        except Exception:
            # BUG FIX: Clean up connection on failure to prevent resource leak
            if hasattr(self, 'conn') and self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
            raise

    def _init_db(self):
        """Create tables if they don't exist"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                document TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)")
        self.conn.commit()

    def get(self, key: str) -> SafeDocument | None:
        """Get document from cache.

        Returns:
            The SafeDocument if found and not expired, None otherwise.
            Note: SQLite serialization/deserialization inherently creates
            a new object, so no additional copy is needed for thread-safety.
        """
        with self._db_lock:
            if self._closed:
                raise RuntimeError("Cannot use closed SQLiteCache instance")
            cursor = self.conn.execute("SELECT document, expires_at FROM cache WHERE key = ?", (key,))
            row = cursor.fetchone()

            if not row:
                return None

            document_json, expires_at = row

            # Check expiration — delete within the same lock to avoid TOCTOU
            if expires_at > 0 and time.time() >= expires_at:
                self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                self.conn.commit()
                return None

            # Deserialize within the lock to:
            # 1. Prevent TOCTOU: another thread could delete/modify entry during deserialization
            # 2. Allow cleanup of corrupt entries before returning
            try:
                return self._deserialize_document(document_json)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                _logger.warning(
                    "Cache deserialization failed for key '%s' — deleting corrupt entry. Error: %s",
                    key[:16] if len(key) > 16 else key,
                    exc,
                )
                # Delete the corrupt entry from database
                self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                self.conn.commit()
                return None

    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """Store document in cache with automatic cleanup when threshold exceeded.

        Args:
            key: Cache key
            document: SafeDocument to store
            ttl: Time-to-live in seconds. If None, uses default_ttl.
                Must be a positive value (> 0).

        Raises:
            ValueError: If TTL is negative or zero

        Note:
            TTL=0 (permanent entries) is no longer supported to prevent
            unbounded disk usage. Use a sufficiently large TTL instead.
        """
        ttl = _validate_ttl_value(ttl if ttl is not None else self.default_ttl, field_name="TTL")
        expires_at = time.time() + ttl

        document_json = self._serialize_document(document)

        with self._db_lock:
            if self._closed:
                raise RuntimeError("Cannot use closed SQLiteCache instance")
            self.conn.execute(
                "INSERT OR REPLACE INTO cache (key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (key, document_json, time.time(), expires_at),
            )
            self.conn.commit()

            # Periodic cleanup: check if we need to clean up expired entries
            if self.cleanup_threshold > 0:
                self._check_and_cleanup_locked()

    def _check_and_cleanup_locked(self) -> None:
        """Check entry count and cleanup expired entries if threshold exceeded.

        Must be called with _db_lock held.
        """
        # Get current entry count
        cursor = self.conn.execute("SELECT COUNT(*) FROM cache")
        count = cursor.fetchone()[0]

        # Cleanup if threshold exceeded
        if count >= self.cleanup_threshold:
            try:
                cursor = self.conn.execute(
                    "DELETE FROM cache WHERE expires_at > 0 AND expires_at < ?",
                    (time.time(),),
                )
                if cursor.rowcount > 0:
                    _logger.debug(
                        "Periodic cleanup removed %d expired entries from SQLite cache",
                        cursor.rowcount,
                    )
                self.conn.commit()
            except Exception as exc:
                _logger.warning("Periodic cleanup failed: %s", exc)

    def delete(self, key: str) -> None:
        """Delete entry from cache."""
        if self._closed:
            raise RuntimeError("Cannot use closed SQLiteCache instance")

        with self._db_lock:
            self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            self.conn.commit()

    def clear(self) -> None:
        """Clear all cache entries."""
        if self._closed:
            raise RuntimeError("Cannot use closed SQLiteCache instance")

        with self._db_lock:
            self.conn.execute("DELETE FROM cache")
            self.conn.commit()

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        return self.get(key) is not None

    def cleanup_expired(self) -> int:
        """Remove expired entries.

        Returns:
            Number of entries removed
        """
        if self._closed:
            raise RuntimeError("Cannot use closed SQLiteCache instance")

        with self._db_lock:
            cursor = self.conn.execute(
                "DELETE FROM cache WHERE expires_at > 0 AND expires_at < ?", (time.time(),)
            )
            self.conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        """Close the database connection explicitly.

        This method should be called when the cache is no longer needed.
        After calling close(), any subsequent operations will raise RuntimeError.

        Example:
            cache = SQLiteCache()
            try:
                cache.set("key", doc)
                doc = cache.get("key")
            finally:
                cache.close()
        """
        # BUG FIX: Remove TOCTOU race - check and set atomically under lock
        with self._db_lock:
            if self._closed:
                return
            self._closed = True
            # Close connection inside the lock to prevent race condition
            # where another thread is using conn.close() while we're closing it
            try:
                self.conn.close()
            except Exception as exc:
                _logger.warning("Error closing SQLite connection: %s", exc)

    def __enter__(self) -> "SQLiteCache":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - ensures connection is closed."""
        self.close()

    def __del__(self):
        """Fallback cleanup - prefer explicit close() or context manager."""
        # Check if we have the necessary attributes before attempting cleanup
        # During garbage collection, attributes may be collected in any order
        if not hasattr(self, "_closed"):
            return
        if not hasattr(self, "conn"):
            return
        if not hasattr(self, "_db_lock"):
            return

        # Use a local reference to avoid issues if self.conn is deleted
        conn = getattr(self, "conn", None)
        if conn is None:
            return

        # Use a local reference to the lock as well
        _db_lock = getattr(self, "_db_lock", None)
        if _db_lock is None:
            return

        # Acquire lock before checking/setting _closed to prevent race condition
        with _db_lock:
            if self._closed:
                return
            self._closed = True
            # Close connection inside the lock for consistency
            try:
                conn.close()
            except Exception as e:
                # Log at debug level - __del__ runs during garbage collection, errors are expected
                _logger.debug("SQLite connection close during __del__ failed: %s", e)

    def _serialize_document(self, doc: SafeDocument) -> str:
        """Serialize SafeDocument to JSON"""
        return json.dumps(
            {
                "markdown": doc.markdown,
                "metadata": doc.metadata,
                "token_estimate": doc.token_estimate,
                "content_hash": doc.content_hash,
                "injection_score": doc.injection_score,
                "flags": doc.flags,
                "removed_elements": doc.removed_elements,
                "screenshot_path": doc.screenshot_path,
                "enriched_metadata": doc.enriched_metadata,
                "links": doc.links,
                "nova_score": doc.nova_score,
                "nova_details": doc.nova_details,
                "structured_blocks": doc.structured_blocks,
                "chunks": doc.chunks,
                "security_explanation": doc.security_explanation,
                "observability": doc.observability,
            }
        )

    def _deserialize_document(self, json_str: str) -> SafeDocument:
        """Deserialize JSON to SafeDocument.

        Args:
            json_str: JSON string to deserialize

        Returns:
            Deserialized SafeDocument

        Raises:
            json.JSONDecodeError: If JSON is malformed
            TypeError: If data structure is invalid
            ValueError: If data values are invalid
        """
        data = json.loads(json_str)
        return SafeDocument(**data)
