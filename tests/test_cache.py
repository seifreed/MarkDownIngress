"""Tests for caching"""

import json
import time

import pytest

from markdown_ingress.adapters.cache.memory import MemoryCache
from markdown_ingress.adapters.cache.sqlite import SQLiteCache
from markdown_ingress.core.cache import Cache
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


def test_cache_key_generation_uses_extra_dimensions():
    extra1 = {"user_agent": "Mozilla/5.0 AgentA"}
    extra2 = {"user_agent": "Mozilla/5.0 AgentB"}

    key1 = Cache.make_key("http://example.com", extra=extra1)
    key2 = Cache.make_key("http://example.com", extra=extra2)

    assert key1 != key2


def test_cache_and_request_identity_normalize_trailing_dot_hosts():
    from markdown_ingress.config_models import DomainPolicy, IngestConfig
    from markdown_ingress.core.inflight import build_request_identity, make_request_key

    config = IngestConfig(mode="fast")
    url_a = "http://example.com/path?x=1"
    url_b = "http://example.com./path?x=1"

    identity_a = build_request_identity(url_a, config)
    identity_b = build_request_identity(url_b, config)

    assert identity_a == identity_b
    assert identity_a["url"] == "http://example.com/path?x=1"
    assert Cache.make_key(
        url_a, mode=config.mode, strict=config.strict, extra=identity_a
    ) == Cache.make_key(
        url_b,
        mode=config.mode,
        strict=config.strict,
        extra=identity_b,
    )
    assert make_request_key(url_a, config) == make_request_key(url_b, config)

    default_port_a = "http://example.com:80/path?x=1"
    default_port_b = "http://example.com/path?x=1"
    assert Cache.make_key(default_port_a, mode=config.mode, strict=config.strict) == Cache.make_key(
        default_port_b,
        mode=config.mode,
        strict=config.strict,
    )
    assert make_request_key(default_port_a, config) == make_request_key(default_port_b, config)

    fragment_a = "http://example.com/path?x=1#one"
    fragment_b = "http://example.com/path?x=1#two"
    assert Cache.make_key(fragment_a, mode=config.mode, strict=config.strict) == Cache.make_key(
        fragment_b,
        mode=config.mode,
        strict=config.strict,
    )
    assert make_request_key(fragment_a, config) == make_request_key(fragment_b, config)

    root_a = "http://example.com"
    root_b = "http://example.com/"
    assert Cache.make_key(root_a, mode=config.mode, strict=config.strict) == Cache.make_key(
        root_b,
        mode=config.mode,
        strict=config.strict,
    )
    assert make_request_key(root_a, config) == make_request_key(root_b, config)

    policy_a = DomainPolicy(domain="example.com")
    policy_b = DomainPolicy(domain="example.com.")
    assert build_request_identity(url_a, config, policy_a) == build_request_identity(
        url_a, config, policy_b
    )


def test_request_identity_distinguishes_domain_policy_notes():
    from markdown_ingress.config_models import DomainPolicy, IngestConfig
    from markdown_ingress.core.inflight import build_request_identity, make_request_key

    config = IngestConfig(mode="fast")
    policy_a = DomainPolicy(domain="example.com", notes="keep this output chunk-friendly")
    policy_b = DomainPolicy(domain="example.com", notes="use a strict security summary")

    identity_a = build_request_identity("http://example.com", config, policy_a)
    identity_b = build_request_identity("http://example.com", config, policy_b)

    assert (
        identity_a["matched_domain_policy"]["notes"] != identity_b["matched_domain_policy"]["notes"]
    )
    assert identity_a != identity_b
    assert make_request_key("http://example.com", config, policy_a) != make_request_key(
        "http://example.com",
        config,
        policy_b,
    )


def test_request_identity_distinguishes_plugin_directory_contents(tmp_path):
    from markdown_ingress.config_models import IngestConfig
    from markdown_ingress.core.inflight import build_request_identity, make_request_key

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "versioned_plugin.py"
    plugin_file.write_text("""
from markdown_ingress.core.plugin import Plugin


class VersionedPlugin(Plugin):
    def get_patterns(self):
        return [r"alpha_marker"]
""".strip())

    config = IngestConfig(mode="fast", plugin_dirs=[str(plugin_dir)])
    identity_a = build_request_identity("http://example.com", config)
    key_a = make_request_key("http://example.com", config)

    plugin_file.write_text("""
from markdown_ingress.core.plugin import Plugin


class VersionedPlugin(Plugin):
    def get_patterns(self):
        return [r"alpha_marker", r"beta_marker"]
""".strip())

    identity_b = build_request_identity("http://example.com", config)

    assert identity_a["plugin_dirs"] != identity_b["plugin_dirs"]
    assert identity_a != identity_b
    assert key_a != make_request_key("http://example.com", config)


def test_request_identity_resolves_allow_local_urls_from_environment(monkeypatch):
    from markdown_ingress.config_models import IngestConfig
    from markdown_ingress.core.inflight import build_request_identity

    monkeypatch.setenv("MDI_ALLOW_LOCAL_URLS", "1")

    implicit_config = IngestConfig(mode="fast")
    explicit_config = IngestConfig(mode="fast", allow_local_urls=True)

    implicit_identity = build_request_identity("http://example.com", implicit_config)
    explicit_identity = build_request_identity("http://example.com", explicit_config)

    assert implicit_identity["allow_local_urls"] is True
    assert implicit_identity == explicit_identity


def test_request_identity_ignores_cache_ttl():
    from markdown_ingress.config_models import IngestConfig
    from markdown_ingress.core.inflight import build_request_identity, make_request_key

    short_ttl = IngestConfig(mode="fast", cache_ttl=10)
    long_ttl = IngestConfig(mode="fast", cache_ttl=100)

    short_identity = build_request_identity("http://example.com", short_ttl)
    long_identity = build_request_identity("http://example.com", long_ttl)

    assert "cache_ttl" not in short_identity
    assert "cache_ttl" not in long_identity
    assert short_identity == long_identity
    assert make_request_key("http://example.com", short_ttl) == make_request_key(
        "http://example.com",
        long_ttl,
    )


def test_request_identity_ignores_cache_backend_presence():
    from markdown_ingress.adapters.cache.memory import MemoryCache
    from markdown_ingress.config_models import IngestConfig
    from markdown_ingress.core.inflight import build_request_identity, make_request_key

    cached = IngestConfig(mode="fast", cache=MemoryCache(default_ttl=60))
    uncached = IngestConfig(mode="fast", cache=None)

    cached_identity = build_request_identity("http://example.com", cached)
    uncached_identity = build_request_identity("http://example.com", uncached)

    assert "cache_enabled" not in cached_identity
    assert "cache_enabled" not in uncached_identity
    assert "cache_backend" not in cached_identity
    assert "cache_backend" not in uncached_identity
    assert cached_identity == uncached_identity
    assert make_request_key("http://example.com", cached) == make_request_key(
        "http://example.com",
        uncached,
    )


def test_request_identity_ignores_cache_backend_details(monkeypatch, tmp_path):
    from markdown_ingress.adapters.cache.memory import MemoryCache
    from markdown_ingress.adapters.cache.sqlite import SQLiteCache
    from markdown_ingress.config_models import IngestConfig
    from markdown_ingress.core.inflight import build_request_identity, make_request_key

    memory_a = IngestConfig(mode="fast", cache=MemoryCache(default_ttl=60, max_entries=10))
    memory_b = IngestConfig(mode="fast", cache=MemoryCache(default_ttl=60, max_entries=10))
    memory_c = IngestConfig(mode="fast", cache=MemoryCache(default_ttl=120, max_entries=10))

    memory_identity_a = build_request_identity("http://example.com", memory_a)
    memory_identity_b = build_request_identity("http://example.com", memory_b)
    memory_identity_c = build_request_identity("http://example.com", memory_c)

    assert memory_identity_a == memory_identity_b
    assert memory_identity_a == memory_identity_c
    assert make_request_key("http://example.com", memory_a) == make_request_key(
        "http://example.com",
        memory_b,
    )
    assert make_request_key("http://example.com", memory_a) == make_request_key(
        "http://example.com",
        memory_c,
    )

    monkeypatch.chdir(tmp_path)
    sqlite_a = None
    sqlite_b = None
    try:
        sqlite_a = IngestConfig(
            mode="fast", cache=SQLiteCache(db_path="cache-a.db", default_ttl=60)
        )
        sqlite_b = IngestConfig(
            mode="fast", cache=SQLiteCache(db_path="cache-b.db", default_ttl=60)
        )

        sqlite_identity_a = build_request_identity("http://example.com", sqlite_a)
        sqlite_identity_b = build_request_identity("http://example.com", sqlite_b)

        assert sqlite_identity_a == sqlite_identity_b
        assert make_request_key("http://example.com", sqlite_a) == make_request_key(
            "http://example.com",
            sqlite_b,
        )
    finally:
        if sqlite_a is not None and getattr(sqlite_a.cache, "close", None) is not None:
            sqlite_a.cache.close()
        if sqlite_b is not None and getattr(sqlite_b.cache, "close", None) is not None:
            sqlite_b.cache.close()


def test_request_identity_ignores_custom_cache_backend_state():
    from markdown_ingress.adapters.cache.memory import MemoryCache
    from markdown_ingress.config_models import IngestConfig
    from markdown_ingress.core.inflight import build_request_identity, make_request_key

    class SlotCache:
        __slots__ = ("name", "size")

        def __init__(self, name: str, size: int):
            self.name = name
            self.size = size

    class PrivateSlotCache:
        __slots__ = ("_namespace",)

        def __init__(self, namespace: str):
            self._namespace = namespace

    class NamespacedMemoryCache(MemoryCache):
        def __init__(self, namespace: str, **kwargs):
            super().__init__(**kwargs)
            self.namespace = namespace

    class PublicHandleCache:
        def __init__(self, namespace: str):
            self.namespace = namespace
            self.handle = object()

    slot_identity_a = build_request_identity(
        "http://example.com",
        IngestConfig(mode="fast", cache=SlotCache("alpha", 1)),
    )
    slot_identity_b = build_request_identity(
        "http://example.com",
        IngestConfig(mode="fast", cache=SlotCache("alpha", 2)),
    )

    assert slot_identity_a == slot_identity_b
    assert make_request_key(
        "http://example.com",
        IngestConfig(mode="fast", cache=SlotCache("alpha", 1)),
    ) == make_request_key(
        "http://example.com",
        IngestConfig(mode="fast", cache=SlotCache("alpha", 2)),
    )

    private_identity_a = build_request_identity(
        "http://example.com",
        IngestConfig(mode="fast", cache=PrivateSlotCache("left")),
    )
    private_identity_b = build_request_identity(
        "http://example.com",
        IngestConfig(mode="fast", cache=PrivateSlotCache("right")),
    )

    assert private_identity_a == private_identity_b
    assert make_request_key(
        "http://example.com",
        IngestConfig(mode="fast", cache=PrivateSlotCache("left")),
    ) == make_request_key(
        "http://example.com",
        IngestConfig(mode="fast", cache=PrivateSlotCache("right")),
    )

    memory_identity_a = build_request_identity(
        "http://example.com",
        IngestConfig(
            mode="fast", cache=NamespacedMemoryCache("left", default_ttl=60, max_entries=10)
        ),
    )
    memory_identity_b = build_request_identity(
        "http://example.com",
        IngestConfig(
            mode="fast", cache=NamespacedMemoryCache("right", default_ttl=60, max_entries=10)
        ),
    )

    assert memory_identity_a == memory_identity_b
    assert make_request_key(
        "http://example.com",
        IngestConfig(
            mode="fast", cache=NamespacedMemoryCache("left", default_ttl=60, max_entries=10)
        ),
    ) == make_request_key(
        "http://example.com",
        IngestConfig(
            mode="fast", cache=NamespacedMemoryCache("right", default_ttl=60, max_entries=10)
        ),
    )

    public_handle_identity_a = build_request_identity(
        "http://example.com",
        IngestConfig(mode="fast", cache=PublicHandleCache("shared")),
    )
    public_handle_identity_b = build_request_identity(
        "http://example.com",
        IngestConfig(mode="fast", cache=PublicHandleCache("shared")),
    )

    assert public_handle_identity_a == public_handle_identity_b
    assert make_request_key(
        "http://example.com",
        IngestConfig(mode="fast", cache=PublicHandleCache("shared")),
    ) == make_request_key(
        "http://example.com",
        IngestConfig(mode="fast", cache=PublicHandleCache("shared")),
    )


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


def test_memory_cache_stats_uses_lock():
    class TrackingLock:
        def __init__(self):
            self.entered = False

        def __enter__(self):
            self.entered = True

        def __exit__(self, exc_type, exc, tb):
            return False

    cache = MemoryCache()
    lock = TrackingLock()
    cache._lock = lock

    stats = cache.stats()

    assert lock.entered is True
    assert stats["size"] == 0


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


@pytest.mark.parametrize("bad_ttl", [True, 1.5, "60"])
def test_memory_cache_rejects_non_integer_ttl(sample_document, bad_ttl):
    cache = MemoryCache()

    with pytest.raises(ValueError, match="TTL must be an int"):
        cache.set("key", sample_document, ttl=bad_ttl)

    with pytest.raises(ValueError, match="default_ttl must be an int"):
        MemoryCache(default_ttl=bad_ttl)


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


@pytest.mark.parametrize("bad_threshold", [True, 1.5, "3"])
def test_sqlite_cache_rejects_non_integer_cleanup_threshold(tmp_path, bad_threshold):
    db_path = tmp_path / "test.db"

    with pytest.raises(ValueError, match="cleanup_threshold must be an int"):
        SQLiteCache(db_path=str(db_path), cleanup_threshold=bad_threshold)


def test_sqlite_cache_rejects_negative_cleanup_threshold(tmp_path):
    db_path = tmp_path / "test.db"

    with pytest.raises(ValueError, match="cleanup_threshold must be >= 0"):
        SQLiteCache(db_path=str(db_path), cleanup_threshold=-1)


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


@pytest.mark.parametrize("bad_ttl", [True, 1.5, "60"])
def test_sqlite_cache_rejects_non_integer_ttl(sample_document, tmp_path, bad_ttl):
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path))

    with pytest.raises(ValueError, match="TTL must be an int"):
        cache.set("key", sample_document, ttl=bad_ttl)

    cache.close()
    with pytest.raises(ValueError, match="default_ttl must be an int"):
        SQLiteCache(db_path=str(db_path), default_ttl=bad_ttl)


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


def test_sqlite_cache_absolute_path_rejection_suppresses_internal_path_cause(tmp_path):
    outside_cwd = tmp_path / "outside.sqlite3"

    with pytest.raises(ValueError, match="Absolute db_path") as exc_info:
        SQLiteCache(db_path=str(outside_cwd), allow_absolute_paths=False)

    assert exc_info.value.__cause__ is None


def test_sqlite_cache_corrupt_entry_deleted(tmp_path, caplog):
    """Test that corrupt entries are deleted from database on deserialization failure"""
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


def test_sqlite_cache_non_object_json_entry_deleted(tmp_path, caplog):
    """Valid JSON with a non-object root is corrupt and should be purged."""
    import logging

    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=3600)

    with cache._db_lock:
        cache.conn.execute(
            "INSERT OR REPLACE INTO cache (key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("list_key", "[1, 2, 3]", 0.0, 0.0),
        )
        cache.conn.commit()

    with caplog.at_level(logging.WARNING):
        result = cache.get("list_key")

    assert result is None
    assert "Cache deserialization failed" in caplog.text
    cursor = cache.conn.execute("SELECT COUNT(*) FROM cache WHERE key = 'list_key'")
    assert cursor.fetchone()[0] == 0

    cache.close()


def test_sqlite_cache_get_deletes_entry_with_corrupt_expires_at(sample_document, tmp_path, caplog):
    import logging

    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=3600)

    with cache._db_lock:
        cache.conn.execute(
            "INSERT OR REPLACE INTO cache (key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("bad_expiry", cache._serialize_document(sample_document), 0.0, "not-a-number"),
        )
        cache.conn.commit()

    with caplog.at_level(logging.WARNING):
        assert cache.get("bad_expiry") is None

    assert "corrupt expires_at" in caplog.text
    cursor = cache.conn.execute("SELECT COUNT(*) FROM cache WHERE key = 'bad_expiry'")
    assert cursor.fetchone()[0] == 0

    cache.close()


def test_sqlite_cache_exists_deletes_entry_with_corrupt_expires_at(sample_document, tmp_path):
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=3600)

    with cache._db_lock:
        cache.conn.execute(
            "INSERT OR REPLACE INTO cache (key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("bad_expiry", cache._serialize_document(sample_document), 0.0, "not-a-number"),
        )
        cache.conn.commit()

    assert cache.exists("bad_expiry") is False
    cursor = cache.conn.execute("SELECT COUNT(*) FROM cache WHERE key = 'bad_expiry'")
    assert cursor.fetchone()[0] == 0

    cache.close()


def test_sqlite_cache_accepts_integer_injection_score_from_legacy_json(tmp_path):
    """Legacy JSON may contain 0 instead of 0.0 for numeric score fields."""
    db_path = tmp_path / "test.db"
    cache = SQLiteCache(db_path=str(db_path), default_ttl=3600)

    legacy_json = json.dumps(
        {
            "markdown": "# Test",
            "metadata": {},
            "token_estimate": 1,
            "content_hash": "sha256:abc",
            "injection_score": 0,
        }
    )
    with cache._db_lock:
        cache.conn.execute(
            "INSERT OR REPLACE INTO cache (key, document, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("legacy_key", legacy_json, 0.0, time.time() + 3600),
        )
        cache.conn.commit()

    result = cache.get("legacy_key")

    assert result is not None
    assert result.injection_score == 0.0
    assert cache.exists("legacy_key")

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


def test_memory_cache_negative_max_entries_raises():
    """Negative max_entries must raise ValueError instead of silently becoming unlimited."""
    with pytest.raises(ValueError, match="max_entries must be >= 0"):
        MemoryCache(max_entries=-1)


@pytest.mark.parametrize("bad_max_entries", [True, 1.5, "10"])
def test_memory_cache_rejects_non_integer_max_entries(bad_max_entries):
    with pytest.raises(ValueError, match="max_entries must be an int"):
        MemoryCache(max_entries=bad_max_entries)
