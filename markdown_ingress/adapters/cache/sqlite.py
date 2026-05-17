"""
SQLite-backed persistent cache implementation.
"""

import json
import logging
import threading
import time
from pathlib import Path

from markdown_ingress.adapters.cache.sqlite_document_codec import (
    deserialize_document,
    serialize_document,
)
from markdown_ingress.adapters.cache.sqlite_entries import (
    cache_key_label,
    coerce_expires_at,
    delete_cache_key,
    delete_expired_entries,
    is_cache_entry_expired,
)
from markdown_ingress.adapters.cache.sqlite_lifecycle import (
    close_connection_after_init_failure,
    close_connection_for_cache,
    close_connection_from_del,
)
from markdown_ingress.adapters.cache.sqlite_path import validate_db_path
from markdown_ingress.adapters.cache.utils import _validate_ttl_value
from markdown_ingress.core.cache import Cache
from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)


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
    def _validate_db_path(db_path: str, allow_absolute_paths: bool = True) -> Path:
        """Validate and resolve the database path.

        Args:
            db_path: Path to SQLite database file
            allow_absolute_paths: If False, reject absolute paths outside CWD for
                stricter security. Defaults to True for backward compatibility.
                Path traversal (e.g., '../../etc/passwd') is always rejected.

        Returns:
            Resolved Path object

        Raises:
            ValueError: If path is empty, attempts path traversal outside
                the current working directory, or is absolute when
                allow_absolute_paths=False
        """
        return validate_db_path(db_path, allow_absolute_paths)

    def __init__(
        self,
        db_path: str = ".cache/markdowningress.db",
        default_ttl: int = 3600,
        cleanup_threshold: int = DEFAULT_CLEANUP_THRESHOLD,
        allow_absolute_paths: bool = True,
    ):
        """
        Initialize SQLite cache.

        Args:
            db_path: Path to SQLite database file. Must be a non-empty string.
                Path traversal (e.g., '../../etc/passwd') is not allowed.
                Relative paths are resolved from the current working directory.
                Absolute paths are allowed by default with a warning log.
            default_ttl: Default TTL in seconds. Must be positive (> 0).
            cleanup_threshold: Number of entries that triggers automatic cleanup
                during set() operations. Set to 0 to disable periodic cleanup.
            allow_absolute_paths: If False, reject absolute paths outside the current
                working directory for stricter security. Defaults to True for
                backward compatibility. Path traversal is always rejected regardless
                of this setting.

        Raises:
            ValueError: If default_ttl is not positive, db_path is empty/invalid,
                path traversal is detected, or an absolute path is provided when
                allow_absolute_paths=False
        """
        import sqlite3

        # Validate and resolve the database path
        self.db_path = self._validate_db_path(db_path, allow_absolute_paths)
        self.default_ttl = _validate_ttl_value(default_ttl, field_name="default_ttl")
        if isinstance(cleanup_threshold, bool) or not isinstance(cleanup_threshold, int):
            raise ValueError(
                f"cleanup_threshold must be an int, got {type(cleanup_threshold).__name__}"
            )
        if cleanup_threshold < 0:
            raise ValueError(f"cleanup_threshold must be >= 0, got {cleanup_threshold}")
        self.cleanup_threshold = cleanup_threshold
        self._closed = False

        # Create directory if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database with proper cleanup on failure
        self._db_lock = threading.Lock()
        try:
            # Re-validate to close the TOCTOU window between initial validation and open.
            self.db_path = self._validate_db_path(str(self.db_path), allow_absolute_paths)
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._init_db()
        except Exception:
            # BUG FIX: Clean up connection on failure to prevent resource leak
            if hasattr(self, "conn") and self.conn:
                close_connection_after_init_failure(self.conn, _logger)
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

    @staticmethod
    def _coerce_expires_at(value: object) -> float | None:
        return coerce_expires_at(value)

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
            cursor = self.conn.execute(
                "SELECT document, expires_at FROM cache WHERE key = ?", (key,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            document_json, expires_at = row
            expires_at_value = self._coerce_expires_at(expires_at)
            if expires_at_value is None:
                _logger.warning(
                    "Cache entry for key '%s' has corrupt expires_at value - deleting entry.",
                    cache_key_label(key),
                )
                delete_cache_key(self.conn, key)
                self.conn.commit()
                return None

            # Check expiration — delete within the same lock to avoid TOCTOU
            if is_cache_entry_expired(expires_at_value):
                delete_cache_key(self.conn, key)
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
                    cache_key_label(key),
                    exc,
                )
                # Delete the corrupt entry from database
                delete_cache_key(self.conn, key)
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
                "INSERT OR REPLACE INTO cache "
                "(key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
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
                rowcount = delete_expired_entries(self.conn)
                if rowcount > 0:
                    _logger.debug(
                        "Periodic cleanup removed %d expired entries from SQLite cache",
                        rowcount,
                    )
                self.conn.commit()
            except Exception as exc:
                _logger.warning("Periodic cleanup failed: %s", exc)

    def delete(self, key: str) -> None:
        """Delete entry from cache."""
        with self._db_lock:
            if self._closed:
                raise RuntimeError("Cannot use closed SQLiteCache instance")
            self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            self.conn.commit()

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._db_lock:
            if self._closed:
                raise RuntimeError("Cannot use closed SQLiteCache instance")
            self.conn.execute("DELETE FROM cache")
            self.conn.commit()

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired without deserializing the document."""
        with self._db_lock:
            if self._closed:
                raise RuntimeError("Cannot use closed SQLiteCache instance")
            now = time.time()
            cursor = self.conn.execute(
                "SELECT expires_at FROM cache WHERE key = ?",
                (key,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            expires_at = self._coerce_expires_at(row[0])
            if expires_at is None:
                delete_cache_key(self.conn, key)
                self.conn.commit()
                return False
            if is_cache_entry_expired(expires_at, now=now):
                delete_cache_key(self.conn, key)
                self.conn.commit()
                return False
            return True

    def cleanup_expired(self) -> int:
        """Remove expired entries.

        Returns:
            Number of entries removed
        """
        with self._db_lock:
            if self._closed:
                raise RuntimeError("Cannot use closed SQLiteCache instance")
            rowcount = delete_expired_entries(self.conn)
            self.conn.commit()
            return rowcount

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
            close_connection_for_cache(self.conn, _logger)

    def __enter__(self) -> "SQLiteCache":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - ensures connection is closed."""
        self.close()

    def __del__(self):
        """Fallback cleanup - prefer explicit close() or context manager."""
        close_connection_from_del(self, _logger)

    def _serialize_document(self, doc: SafeDocument) -> str:
        """Serialize SafeDocument to JSON"""
        return serialize_document(doc)

    def _deserialize_document(self, json_str: str) -> SafeDocument:
        """Deserialize JSON to SafeDocument.

        Robust against schema evolution: filters out unknown keys (forward
        compatibility) and provides defaults for missing optional fields
        (backward compatibility).

        Args:
            json_str: JSON string to deserialize

        Returns:
            Deserialized SafeDocument

        Raises:
            json.JSONDecodeError: If JSON is malformed
            TypeError: If required fields are missing and have no default
            ValueError: If data values are invalid
        """
        return deserialize_document(json_str)
