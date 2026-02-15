"""
Caching layer for processed documents
"""

import hashlib
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from markdown_ingress.models import SafeDocument


class Cache(ABC):
    """Abstract cache interface"""

    @abstractmethod
    def get(self, key: str) -> SafeDocument | None:
        """Get document from cache"""
        pass

    @abstractmethod
    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """Store document in cache"""
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete document from cache"""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear entire cache"""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        pass

    @staticmethod
    def make_key(url: str, mode: str = "fast", strict: bool = True) -> str:
        """
        Generate cache key from URL and parameters.

        Args:
            url: Source URL
            mode: Fetching mode
            strict: Strict mode flag

        Returns:
            Cache key string
        """
        key_data = f"{url}:{mode}:{strict}"
        return hashlib.sha256(key_data.encode()).hexdigest()


class MemoryCache(Cache):
    """In-memory cache implementation"""

    def __init__(self, default_ttl: int = 3600):
        """
        Initialize memory cache.

        Args:
            default_ttl: Default time-to-live in seconds (0 = no expiration)
        """
        self.default_ttl = default_ttl
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> SafeDocument | None:
        """Get document from cache if not expired"""
        if key not in self._cache:
            return None

        entry = self._cache[key]

        # Check expiration
        if entry["expires_at"] > 0 and time.time() > entry["expires_at"]:
            del self._cache[key]
            return None

        return entry["document"]

    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """Store document in cache"""
        ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + ttl if ttl > 0 else 0

        self._cache[key] = {
            "document": document,
            "expires_at": expires_at,
            "created_at": time.time(),
        }

    def delete(self, key: str) -> None:
        """Delete entry from cache"""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries"""
        self._cache.clear()

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired"""
        return self.get(key) is not None

    def cleanup_expired(self) -> int:
        """
        Remove expired entries from cache.

        Returns:
            Number of entries removed
        """
        now = time.time()
        expired_keys = [
            key
            for key, entry in self._cache.items()
            if entry["expires_at"] > 0 and now > entry["expires_at"]
        ]

        for key in expired_keys:
            del self._cache[key]

        return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        return {
            "size": len(self._cache),
            "default_ttl": self.default_ttl,
        }


class SQLiteCache(Cache):
    """SQLite-based persistent cache"""

    def __init__(self, db_path: str = ".cache/markdowningress.db", default_ttl: int = 3600):
        """
        Initialize SQLite cache.

        Args:
            db_path: Path to SQLite database file
            default_ttl: Default TTL in seconds
        """
        import sqlite3

        self.db_path = Path(db_path)
        self.default_ttl = default_ttl

        # Create directory if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self.conn = sqlite3.connect(str(self.db_path))
        self._init_db()

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
        """Get document from cache"""
        cursor = self.conn.execute("SELECT document, expires_at FROM cache WHERE key = ?", (key,))
        row = cursor.fetchone()

        if not row:
            return None

        document_json, expires_at = row

        # Check expiration
        if expires_at > 0 and time.time() > expires_at:
            self.delete(key)
            return None

        # Deserialize document
        return self._deserialize_document(document_json)

    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """Store document in cache"""
        ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + ttl if ttl > 0 else 0

        document_json = self._serialize_document(document)

        self.conn.execute(
            "INSERT OR REPLACE INTO cache (key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (key, document_json, time.time(), expires_at),
        )
        self.conn.commit()

    def delete(self, key: str) -> None:
        """Delete entry from cache"""
        self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        self.conn.commit()

    def clear(self) -> None:
        """Clear all cache entries"""
        self.conn.execute("DELETE FROM cache")
        self.conn.commit()

    def exists(self, key: str) -> bool:
        """Check if key exists"""
        return self.get(key) is not None

    def cleanup_expired(self) -> int:
        """Remove expired entries"""
        cursor = self.conn.execute(
            "DELETE FROM cache WHERE expires_at > 0 AND expires_at < ?", (time.time(),)
        )
        self.conn.commit()
        return cursor.rowcount

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
            }
        )

    def _deserialize_document(self, json_str: str) -> SafeDocument:
        """Deserialize JSON to SafeDocument"""
        data = json.loads(json_str)
        return SafeDocument(**data)

    def __del__(self):
        """Close database connection"""
        if hasattr(self, "conn"):
            self.conn.close()
