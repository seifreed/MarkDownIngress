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

from markdown_ingress.core.ssrf import normalize_url_for_identity
from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)
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

    if type(cache_backend) is MemoryCache:
        identity["default_ttl"] = cache_backend.default_ttl
        identity["max_entries"] = cache_backend.max_entries
    elif type(cache_backend) is SQLiteCache:
        identity["db_path"] = str(cache_backend.db_path)
        identity["default_ttl"] = cache_backend.default_ttl
        identity["cleanup_threshold"] = cache_backend.cleanup_threshold
    public_attrs = _collect_public_identity_attrs(cache_backend)
    for key in list(identity):
        public_attrs.pop(key, None)
    if public_attrs:
        identity["attrs"] = public_attrs

    return identity


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
            if self.max_entries > 0 and len(self._cache) >= self.max_entries and key not in self._cache:
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
        _PRIMITIVE_FIELD_TYPES: dict[str, type] = {
            "markdown": str,
            "title": (str, type(None)),
            "url": str,
            "token_estimate": int,
            "injection_score": float,
            "content_hash": (str, type(None)),
        }
        for field_name, expected in _PRIMITIVE_FIELD_TYPES.items():
            if field_name in filtered and not isinstance(filtered[field_name], expected):
                raise ValueError(
                    f"Cache entry field '{field_name}' has unexpected type "
                    f"{type(filtered[field_name]).__name__}"
                )
        return SafeDocument(**filtered)
