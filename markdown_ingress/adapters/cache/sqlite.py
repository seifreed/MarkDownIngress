"""
SQLite-backed persistent cache implementation.
"""

import json
import logging
import threading
import time
from pathlib import Path

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
                # BUG FIX: Optionally reject absolute paths for stricter security
                if not allow_absolute_paths:
                    raise ValueError(
                        f"Absolute db_path '{db_path}' not allowed. "
                        f"Resolved path: '{resolved_path}', Working directory: '{cwd}'. "
                        "Set allow_absolute_paths=True to permit absolute paths."
                    )
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
                try:
                    self.conn.close()
                except Exception as exc:
                    _logger.debug("Cache connection close during init cleanup failed: %s", exc)
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
            cursor = self.conn.execute(
                "SELECT document, expires_at FROM cache WHERE key = ?", (key,)
            )
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
                    "DELETE FROM cache WHERE expires_at > 0 AND expires_at <= ?",
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
            expires_at = row[0]
            if expires_at > 0 and now >= expires_at:
                self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
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
            cursor = self.conn.execute(
                "DELETE FROM cache WHERE expires_at > 0 AND expires_at <= ?", (time.time(),)
            )
            self.conn.commit()
            return max(0, cursor.rowcount)

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

        # Use non-blocking acquire to avoid deadlocking GC against threads holding the lock.
        if not _db_lock.acquire(blocking=False):
            _logger.debug("SQLiteCache.__del__: could not acquire lock, skipping cleanup")
            return
        try:
            if self._closed:
                return
            self._closed = True
            try:
                conn.close()
            except Exception as e:
                _logger.debug("SQLite connection close during __del__ failed: %s", e)
        finally:
            _db_lock.release()

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
        import dataclasses

        data = json.loads(json_str)
        # Filter out keys no longer in the schema (forward compat)
        known_fields = {f.name for f in dataclasses.fields(SafeDocument)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        # Supply defaults for missing fields that have them (backward compat)
        for f in dataclasses.fields(SafeDocument):
            if f.name not in filtered:
                if f.default is not dataclasses.MISSING:
                    filtered[f.name] = f.default
                elif f.default_factory is not dataclasses.MISSING:
                    filtered[f.name] = f.default_factory()
        # Basic type validation for primitive fields to reject corrupted cache entries.
        _primitive_field_types: dict[str, type] = {
            "markdown": str,
            "title": (str, type(None)),
            "url": str,
            "token_estimate": int,
            "injection_score": float,
            "content_hash": (str, type(None)),
        }
        for field_name, expected in _primitive_field_types.items():
            if field_name in filtered and not isinstance(filtered[field_name], expected):
                raise ValueError(
                    f"Cache entry field '{field_name}' has unexpected type "
                    f"{type(filtered[field_name]).__name__}"
                )
        return SafeDocument(**filtered)
