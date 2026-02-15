"""Tests for caching"""

import pytest
import time
from markdown_ingress.core.cache import Cache, MemoryCache, SQLiteCache
from markdown_ingress.models import SafeDocument


@pytest.fixture
def sample_document():
    """Create a sample SafeDocument for testing"""
    return SafeDocument(
        markdown="# Test\n\nContent here.",
        metadata={'url': 'http://example.com', 'title': 'Test'},
        token_estimate=10,
        content_hash="sha256:abc123",
        injection_score=0.1,
        flags=[],
        removed_elements={}
    )


def test_cache_key_generation():
    """Test cache key generation"""
    key1 = Cache.make_key("http://example.com", mode="fast", strict=True)
    key2 = Cache.make_key("http://example.com", mode="fast", strict=True)
    key3 = Cache.make_key("http://example.com", mode="render", strict=True)
    
    # Same params = same key
    assert key1 == key2
    
    # Different params = different key
    assert key1 != key3


def test_memory_cache_basic(sample_document):
    """Test basic memory cache operations"""
    cache = MemoryCache(default_ttl=0)  # No expiration
    
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
    
    assert cache.stats()['size'] == 5
    
    # Wait for expiration
    time.sleep(1.5)
    
    # Cleanup expired
    removed = cache.cleanup_expired()
    assert removed == 5
    assert cache.stats()['size'] == 0


def test_memory_cache_clear(sample_document):
    """Test clearing entire cache"""
    cache = MemoryCache()
    
    cache.set("key1", sample_document)
    cache.set("key2", sample_document)
    
    assert cache.stats()['size'] == 2
    
    cache.clear()
    assert cache.stats()['size'] == 0


def test_sqlite_cache_basic(sample_document, tmp_path):
    """Test basic SQLite cache operations"""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=0)
    
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
    cache1 = SQLiteCache(db_path=str(db_path), default_ttl=0)
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
