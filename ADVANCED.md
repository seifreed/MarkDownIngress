# MarkDownIngress - Advanced Usage Examples (v0.3+)

## Batch Processing

### Basic Batch Processing

```python
from markdown_ingress import BatchProcessor

urls = [
    "https://example.com/article1",
    "https://example.com/article2",
    "https://example.com/article3",
]

# Create batch processor
processor = BatchProcessor(
    mode="fast",
    strict=True,
    max_concurrent=5  # Process 5 URLs concurrently
)

# Process all URLs
result = processor.process_batch(urls)

print(f"Total: {result.total}")
print(f"Successful: {result.successful}")
print(f"Failed: {result.failed}")
print(f"Success rate: {result.success_rate}%")

# Access documents
for doc in result.documents:
    print(f"- {doc.metadata['title']}: {doc.token_estimate} tokens")

# Check errors
for url, error in result.errors.items():
    print(f"Error on {url}: {error}")
```

### Batch with Progress Tracking

```python
from markdown_ingress import BatchProcessor

def on_progress(current, total, url):
    print(f"[{current}/{total}] Processing: {url}")

processor = BatchProcessor(
    mode="fast",
    on_progress=on_progress
)

urls = ["https://example.com"] * 10
result = processor.process_batch(urls)
```

### Async Batch Processing

```python
import asyncio
from markdown_ingress import BatchProcessor

async def main():
    urls = ["https://example.com/page1", "https://example.com/page2"]
    
    processor = BatchProcessor(mode="fast", max_concurrent=10)
    result = await processor.process_batch_async(urls)
    
    return result

result = asyncio.run(main())
```

---

## Caching

### Memory Cache

```python
from markdown_ingress import ingest, MemoryCache, Cache

# Create memory cache with 1-hour TTL
cache = MemoryCache(default_ttl=3600)

url = "https://example.com"

# Generate cache key
key = Cache.make_key(url, mode="fast", strict=True)

# Check cache
cached_doc = cache.get(key)
if cached_doc:
    print("Using cached document")
    doc = cached_doc
else:
    print("Fetching fresh document")
    doc = ingest(url, mode="fast")
    cache.set(key, doc)

# Use document
print(doc.markdown)
```

### SQLite Persistent Cache

```python
from markdown_ingress import ingest, SQLiteCache, Cache

# Create SQLite cache (persists across runs)
cache = SQLiteCache(
    db_path=".cache/markdowningress.db",
    default_ttl=86400  # 24 hours
)

url = "https://example.com"
key = Cache.make_key(url)

# Try cache first
doc = cache.get(key)
if not doc:
    doc = ingest(url)
    cache.set(key, doc)

# Cleanup expired entries periodically
removed = cache.cleanup_expired()
print(f"Removed {removed} expired entries")
```

### Batch Processing with Cache

```python
from markdown_ingress import BatchProcessor, MemoryCache, Cache

cache = MemoryCache(default_ttl=3600)
urls = ["https://example.com", "https://httpbin.org/html"]

# Check cache and filter URLs
urls_to_fetch = []
cached_docs = []

for url in urls:
    key = Cache.make_key(url, mode="fast")
    doc = cache.get(key)
    if doc:
        cached_docs.append(doc)
    else:
        urls_to_fetch.append(url)

# Fetch only uncached URLs
if urls_to_fetch:
    processor = BatchProcessor(mode="fast")
    result = processor.process_batch(urls_to_fetch)
    
    # Cache new documents
    for doc in result.documents:
        key = Cache.make_key(doc.metadata['url'], mode="fast")
        cache.set(key, doc)
    
    all_docs = cached_docs + result.documents
else:
    all_docs = cached_docs

print(f"Total documents: {len(all_docs)}")
print(f"From cache: {len(cached_docs)}")
print(f"Freshly fetched: {len(urls_to_fetch)}")
```

---

## Policy Engine

### Using Predefined Policies

```python
from markdown_ingress import ingest, PolicyEngine

# Permissive policy (minimal blocking)
doc = ingest("https://example.com")
policy = PolicyEngine.from_name('permissive')
action = policy.get_action(doc.injection_score)
print(f"Action: {action}")  # 'allow', 'warn', or 'block'

# Paranoid policy (maximum security)
policy_paranoid = PolicyEngine.from_name('paranoid')
if policy_paranoid.should_block(doc.injection_score):
    print("⚠️ Content blocked by paranoid policy")
```

### Custom Policy

```python
from markdown_ingress import Policy, PolicyEngine
from markdown_ingress.core.security import InjectionPattern

# Create custom policy
my_policy = Policy(
    name="my_custom_policy",
    description="Custom security rules for my use case",
    block_threshold=0.65,
    warn_threshold=0.35,
    strictness="normal",
    check_hidden_content=True,
    custom_patterns=[
        InjectionPattern(
            pattern=r'\bmy_custom_attack_pattern\b',
            weight=0.85,
            description="My custom attack detection"
        )
    ]
)

engine = PolicyEngine(policy=my_policy)

# Use with ingestion
doc = ingest("https://example.com")
if engine.should_block(doc.injection_score):
    print("❌ Blocked by custom policy")
elif engine.should_warn(doc.injection_score):
    print("⚠️ Warning from custom policy")
else:
    print("✅ Allowed by custom policy")
```

### Policy from Configuration

```python
from markdown_ingress import PolicyEngine

# Load from dict (could be from JSON/YAML file)
config = {
    'name': 'production',
    'description': 'Production security policy',
    'block_threshold': 0.6,
    'warn_threshold': 0.3,
    'strictness': 'strict',
    'check_hidden_content': True,
}

policy = PolicyEngine.from_dict(config)

# Export policy
policy_dict = policy.to_dict()
print(policy_dict)
```

---

## Complete Integration Example

### RAG Pipeline with Caching and Batch Processing

```python
from markdown_ingress import BatchProcessor, SQLiteCache, PolicyEngine, Cache

# Setup
cache = SQLiteCache(db_path=".cache/rag.db", default_ttl=86400)
policy = PolicyEngine.from_name('strict')

urls = [
    "https://docs.example.com/api",
    "https://docs.example.com/guide",
    "https://docs.example.com/tutorial",
]

# Filter cached URLs
fresh_urls = []
docs = []

for url in urls:
    key = Cache.make_key(url, mode="fast", strict=True)
    doc = cache.get(key)
    
    if doc:
        print(f"✓ Cached: {url}")
        docs.append(doc)
    else:
        fresh_urls.append(url)

# Batch fetch uncached URLs
if fresh_urls:
    print(f"\nFetching {len(fresh_urls)} fresh documents...")
    
    processor = BatchProcessor(
        mode="fast",
        strict=True,
        max_concurrent=5
    )
    
    result = processor.process_batch(fresh_urls)
    
    # Filter by policy
    for doc in result.documents:
        action = policy.get_action(doc.injection_score)
        
        if action == 'block':
            print(f"❌ Blocked: {doc.metadata['url']} (score: {doc.injection_score})")
            continue
        elif action == 'warn':
            print(f"⚠️ Warning: {doc.metadata['url']} (score: {doc.injection_score})")
        
        # Cache and store
        key = Cache.make_key(doc.metadata['url'], mode="fast", strict=True)
        cache.set(key, doc)
        docs.append(doc)

# Process documents for RAG
print(f"\n✓ Total safe documents: {len(docs)}")
total_tokens = sum(doc.token_estimate for doc in docs)
print(f"✓ Total tokens: {total_tokens:,}")

# Generate embeddings, store in vector DB, etc.
for doc in docs:
    print(f"- {doc.metadata['title']}: {doc.token_estimate} tokens")
```

---

## Production Monitoring

### Track Performance Metrics

```python
from markdown_ingress import BatchProcessor, MemoryCache
import time

cache = MemoryCache()
urls = ["https://example.com"] * 100

# Warm up cache
processor = BatchProcessor(mode="fast", max_concurrent=10)
result = processor.process_batch(urls[:10])

for doc in result.documents:
    key = Cache.make_key(doc.metadata['url'])
    cache.set(key, doc)

# Measure cache hit rate
start = time.time()
cache_hits = 0
cache_misses = 0

for url in urls:
    key = Cache.make_key(url)
    if cache.get(key):
        cache_hits += 1
    else:
        cache_misses += 1

elapsed = time.time() - start

print(f"Cache hit rate: {cache_hits / len(urls) * 100:.1f}%")
print(f"Lookup time: {elapsed * 1000:.2f}ms")
```
