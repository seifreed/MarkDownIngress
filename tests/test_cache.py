"""Tests for caching"""

import copy
import time

import pytest

from markdown_ingress.core.cache import Cache, MemoryCache, SQLiteCache
from markdown_ingress.models import SafeDocument


@pytest.fixture
def sample_document():
    """Create a sample SafeDocument for testing"""
    return SafeDocument(
        markdown="# Test\n\nContent here.",
        metadata={"url": "http://example.com", "title": "Test"},
        token_estimate=10,
        content_hash="sha256:abc123",
        injection_score=0.1,
        flags=[],
        removed_elements={},
    )


def test_cache_key_generation():
    """Test cache key generation"""
    key1 = Cache.make_key("http://example.com", mode="fast", strict=True)
    key2 = Cache.make_key("http://example.com", mode="fast", strict=True)
    key3 = Cache.make_key("http://example.com", mode="render", strict=True)
    key4 = Cache.make_key(
        "http://example.com",
        mode="fast",
        strict=True,
        extra={"output_profile": "default"},
    )
    key5 = Cache.make_key(
        "http://example.com",
        mode="fast",
        strict=True,
        extra={"output_profile": "rag_chunkable"},
    )

    # Same params = same key
    assert key1 == key2

    # Different params = different key
    assert key1 != key3
    assert key4 != key5


def test_cache_key_generation_uses_full_user_agent_entropy():
    base = "Mozilla/5.0 (compatible; MarkDownIngressTest/"
    ua1 = base + "A" * 80
    ua2 = base + "A" * 79 + "B"

    key1 = Cache.make_key("http://example.com", user_agent=ua1)
    key2 = Cache.make_key("http://example.com", user_agent=ua2)

    assert key1 != key2


def test_memory_cache_basic(sample_document):
    """Test basic memory cache operations"""
    cache = MemoryCache(default_ttl=3600)

    key = "test_key"

    # Initially empty
    assert not cache.exists(key)
    assert cache.get(key) is None

    # Set and get
    cache.set(key, sample_document)
    assert cache.exists(key)

    retrieved = cache.get(key)
    assert retrieved is not None
    assert retrieved.markdown == sample_document.markdown
    assert retrieved.content_hash == sample_document.content_hash

    # Delete
    cache.delete(key)
    assert not cache.exists(key)


def test_memory_cache_ttl(sample_document):
    """Test TTL expiration in memory cache"""
    cache = MemoryCache(default_ttl=1)  # 1 second TTL

    key = "test_key"
    cache.set(key, sample_document)

    # Should exist immediately
    assert cache.exists(key)

    # Wait for expiration
    time.sleep(1.5)

    # Should be expired
    assert not cache.exists(key)
    assert cache.get(key) is None


def test_memory_cache_cleanup(sample_document):
    """Test cleanup of expired entries"""
    cache = MemoryCache(default_ttl=1)

    # Add multiple entries
    for i in range(5):
        cache.set(f"key_{i}", sample_document)

    assert cache.stats()["size"] == 5

    # Wait for expiration
    time.sleep(1.5)

    # Cleanup expired
    removed = cache.cleanup_expired()
    assert removed == 5
    assert cache.stats()["size"] == 0


def test_memory_cache_clear(sample_document):
    """Test clearing entire cache"""
    cache = MemoryCache()

    cache.set("key1", sample_document)
    cache.set("key2", sample_document)

    assert cache.stats()["size"] == 2

    cache.clear()
    assert cache.stats()["size"] == 0


def test_sqlite_cache_basic(sample_document, tmp_path):
    """Test basic SQLite cache operations"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=3600)

    key = "test_key"

    # Initially empty
    assert not cache.exists(key)

    # Set and get
    cache.set(key, sample_document)
    assert cache.exists(key)

    retrieved = cache.get(key)
    assert retrieved is not None
    assert retrieved.markdown == sample_document.markdown

    # Delete
    cache.delete(key)
    assert not cache.exists(key)


def test_sqlite_cache_persistence(sample_document, tmp_path):
    """Test SQLite cache persistence across instances"""
    db_path = tmp_path / "test.db"

    # First cache instance
    cache1 = SQLiteCache(db_path=str(db_path), default_ttl=3600)
    cache1.set("persistent_key", sample_document)
    del cache1

    # Second cache instance (same DB)
    cache2 = SQLiteCache(db_path=str(db_path))
    retrieved = cache2.get("persistent_key")

    assert retrieved is not None
    assert retrieved.markdown == sample_document.markdown


def test_sqlite_cache_ttl(sample_document, tmp_path):
    """Test TTL in SQLite cache"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=1)

    key = "test_key"
    cache.set(key, sample_document)

    # Should exist immediately
    assert cache.exists(key)

    # Wait for expiration
    time.sleep(1.5)

    # Should be expired
    assert not cache.exists(key)


def test_sqlite_cache_cleanup(sample_document, tmp_path):
    """Test cleanup in SQLite cache"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=1)

    # Add entries
    for i in range(3):
        cache.set(f"key_{i}", sample_document)

    # Wait for expiration
    time.sleep(1.5)

    # Cleanup
    removed = cache.cleanup_expired()
    assert removed == 3


def test_memory_cache_returns_copy(sample_document):
    """Test that MemoryCache.get() returns a copy to prevent TOCTOU issues"""
    cache = MemoryCache(default_ttl=3600)
    key = "test_key"

    # Set document
    cache.set(key, sample_document)

    # Get document twice
    doc1 = cache.get(key)
    doc2 = cache.get(key)

    assert doc1 is not None
    assert doc2 is not None

    # Verify they are different objects (copy returned)
    assert doc1 is not doc2

    # Verify modifying one doesn't affect the other
    doc1.markdown = "Modified content"
    assert doc2.markdown == sample_document.markdown


def test_memory_cache_set_isolates_stored_document(sample_document):
    cache = MemoryCache(default_ttl=3600)

    cache.set("key", sample_document)
    sample_document.markdown = "Mutated after cache set"

    cached = cache.get("key")

    assert cached is not None
    assert cached.markdown == "# Test\n\nContent here."


def test_memory_cache_zero_max_entries_is_unlimited(sample_document):
    cache = MemoryCache(default_ttl=3600, max_entries=0)

    for i in range(110):
        cache.set(f"key_{i}", sample_document)

    assert cache.stats()["size"] == 110
    assert cache.get("key_0") is not None
    assert cache.get("key_109") is not None


def test_memory_cache_ttl_zero_raises():
    """Test that TTL=0 is rejected to avoid permanent entries."""
    with pytest.raises(ValueError, match="positive"):
        MemoryCache(default_ttl=0)


def test_memory_cache_negative_ttl_raises():
    """Test that negative TTL raises ValueError"""
    cache = MemoryCache()

    with pytest.raises(ValueError, match="positive"):
        cache.set("key", None, ttl=-1)

    with pytest.raises(ValueError, match="positive"):
        MemoryCache(default_ttl=-1)


def test_sqlite_cache_context_manager(sample_document, tmp_path):
    """Test SQLiteCache context manager"""
    db_path = tmp_path / "test.db"

    with SQLiteCache(db_path=str(db_path)) as cache:
        cache.set("key1", sample_document)
        assert cache.exists("key1")

    # After context exit, connection should be closed
    assert cache._closed


def test_sqlite_cache_close_method(sample_document, tmp_path):
    """Test SQLiteCache close() method"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path))

    cache.set("key1", sample_document)
    assert cache.exists("key1")

    # Close the cache
    cache.close()
    assert cache._closed

    # Using closed cache should raise RuntimeError
    with pytest.raises(RuntimeError, match="Cannot use closed SQLiteCache"):
        cache.get("key1")


def test_sqlite_cache_double_close_safe(sample_document, tmp_path):
    """Test that calling close() twice is safe"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path))

    cache.set("key1", sample_document)

    # Close twice should be safe
    cache.close()
    cache.close()  # Should not raise

    assert cache._closed


def test_sqlite_cache_periodic_cleanup(sample_document, tmp_path):
    """Test that SQLiteCache performs periodic cleanup on set() when threshold exceeded"""
    db_path = tmp_path / "test.db"
    # Set a low threshold for testing
    cache = SQLiteCache(db_path=str(db_path), default_ttl=1, cleanup_threshold=3)

    # Add entries below threshold
    for i in range(2):
        cache.set(f"key_{i}", sample_document)

    # Wait for expiration
    time.sleep(1.5)

    # Add more entries to trigger cleanup (threshold is 3)
    for i in range(2, 5):
        cache.set(f"key_{i}", sample_document, ttl=1000)

    # Expired entries should have been cleaned up
    # The first 2 entries (key_0, key_1) should have been expired and cleaned
    assert not cache.exists("key_0")
    assert not cache.exists("key_1")

    # New entries should still exist
    assert cache.exists("key_2")
    assert cache.exists("key_3")
    assert cache.exists("key_4")

    cache.close()


def test_sqlite_cache_cleanup_threshold_zero(sample_document, tmp_path):
    """Test that cleanup_threshold=0 disables periodic cleanup"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=1, cleanup_threshold=0)

    # Add entries
    for i in range(5):
        cache.set(f"key_{i}", sample_document)

    # Wait for expiration
    time.sleep(1.5)

    # Add more entries - cleanup should not run (threshold=0)
    cache.set("key_5", sample_document, ttl=1000)

    # Entries should still be in DB (expired but not cleaned up)
    # get() will return None for expired, but they're still in DB
    assert cache.get("key_0") is None

    # Manual cleanup should still work
    removed = cache.cleanup_expired()
    assert removed >= 1  # At least one expired entry

    cache.close()


def test_sqlite_cache_negative_ttl_raises(tmp_path):
    """Test that negative TTL raises ValueError in SQLiteCache"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path))

    sample_doc = SafeDocument(
        markdown="# Test",
        metadata={},
        token_estimate=1,
        content_hash="sha256:abc",
        injection_score=0.1,
        flags=[],
        removed_elements={},
    )

    with pytest.raises(ValueError, match="positive"):
        cache.set("key", sample_doc, ttl=-1)

    with pytest.raises(ValueError, match="positive"):
        SQLiteCache(db_path=str(db_path), default_ttl=-1)

    cache.close()


def test_sqlite_cache_ttl_zero_raises(tmp_path):
    """Test that TTL=0 is rejected in SQLiteCache as well."""
    db_path = tmp_path / "test.db"

    with pytest.raises(ValueError, match="positive"):
        SQLiteCache(db_path=str(db_path), default_ttl=0)


def test_sqlite_cache_path_validation_empty():
    """Test that empty db_path raises ValueError"""
    with pytest.raises(ValueError, match="db_path cannot be empty"):
        SQLiteCache(db_path="")


def test_sqlite_cache_path_validation_whitespace():
    """Test that whitespace-only db_path raises ValueError"""
    with pytest.raises(ValueError, match="db_path cannot be empty"):
        SQLiteCache(db_path="   ")


def test_sqlite_cache_path_traversal_blocked():
    """Test that path traversal (e.g., ../../etc) is blocked"""
    with pytest.raises(ValueError, match="Path traversal is not allowed"):
        SQLiteCache(db_path="../../etc/malicious.db")


def test_sqlite_cache_corrupt_entry_deleted(tmp_path, caplog):
    """Test that corrupt entries are deleted from database on deserialization failure"""
    import json
    import logging

    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=3600)

    sample_doc = SafeDocument(
        markdown="# Test",
        metadata={},
        token_estimate=1,
        content_hash="sha256:abc",
        injection_score=0.1,
        flags=[],
        removed_elements={},
    )

    # Insert a valid entry
    cache.set("valid_key", sample_doc)

    # Insert a corrupt entry directly into the database
    corrupt_json = '{"invalid": "data", "missing_required_fields": true}'
    with cache._db_lock:
        cache.conn.execute(
            "INSERT OR REPLACE INTO cache (key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("corrupt_key", corrupt_json, 0.0, 0.0),
        )
        cache.conn.commit()

    # Verify corrupt entry exists
    cursor = cache.conn.execute("SELECT COUNT(*) FROM cache WHERE key = 'corrupt_key'")
    assert cursor.fetchone()[0] == 1

    # Attempt to get corrupt entry - should return None and delete the entry
    with caplog.at_level(logging.WARNING):
        result = cache.get("corrupt_key")

    assert result is None
    assert "Cache deserialization failed" in caplog.text or "corrupt entry" in caplog.text

    # Verify corrupt entry was deleted
    cursor = cache.conn.execute("SELECT COUNT(*) FROM cache WHERE key = 'corrupt_key'")
    assert cursor.fetchone()[0] == 0

    # Valid entry should still exist
    assert cache.exists("valid_key")

    cache.close()


def test_memory_cache_returns_deep_copy(sample_document):
    """Test that MemoryCache.get() returns a deep copy to prevent TOCTOU issues with nested objects"""
    cache = MemoryCache(default_ttl=3600)
    key = "test_key"

    # Set document with nested metadata
    original_metadata = {"nested": {"deep": "value"}, "list": [1, 2, 3]}
    sample_document.metadata = original_metadata
    cache.set(key, sample_document)

    # Get document and modify nested structure
    doc1 = cache.get(key)
    assert doc1 is not None
    doc1.metadata["nested"]["deep"] = "modified"
    doc1.metadata["list"].append(4)

    # Get again and verify original is unchanged
    doc2 = cache.get(key)
    assert doc2 is not None
    assert doc2.metadata["nested"]["deep"] == "value"
    assert doc2.metadata["list"] == [1, 2, 3]
