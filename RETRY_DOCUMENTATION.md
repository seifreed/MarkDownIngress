# Retry Logic with Exponential Backoff

## Overview

The `retry_ingest()` function provides automatic retry logic with exponential backoff for handling transient network failures, timeouts, and other temporary errors when ingesting web content.

## Features

- **Automatic Retries**: Configurable retry attempts (default: 3)
- **Exponential Backoff**: Wait time increases between retries (1s, 2s, 4s)
- **Timeout Escalation**: Timeout increases on each retry (60s → 90s → 120s)
- **Stealth Mode**: Optional stealth mode enabled on retry attempts
- **Detailed Metadata**: Tracks retry attempts and configuration
- **Smart Error Detection**: Only retries on transient errors

## Usage

### Basic Usage

```python
from markdown_ingress import retry_ingest

# Simple retry with defaults
doc = retry_ingest("https://example.com")

# Access retry metadata
print(f"Attempts: {doc.metadata['retry_attempts']}")
print(f"Final timeout: {doc.metadata['final_timeout']}s")
```

### Advanced Configuration

```python
# Custom retry configuration
doc = retry_ingest(
    url="https://slow-site.com",
    mode="render",              # Use render mode for SPAs
    max_retries=5,               # Try up to 5 times
    initial_timeout=90.0,        # Start with 90s timeout
    enable_stealth=True          # Enable stealth on retries
)
```

### Retry Metadata

The function adds three metadata fields to the returned `SafeDocument`:

```python
doc.metadata['retry_attempts']    # Number of attempts (1 = success on first try)
doc.metadata['retry_enabled']     # Whether stealth was enabled
doc.metadata['final_timeout']     # Final timeout value used (in seconds)
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | str | *required* | Target URL to ingest |
| `mode` | Literal | `"auto"` | Ingestion mode: `"fast"`, `"render"`, or `"auto"` |
| `strict` | bool | `True` | Enable strict security mode |
| `model` | str | `"gpt-4"` | LLM model for token estimation |
| `max_retries` | int | `3` | Maximum retry attempts |
| `enable_stealth` | bool | `True` | Enable stealth mode on retries |
| `initial_timeout` | float | `60.0` | Initial timeout in seconds |

## Retry Logic

### Timeout Escalation

Each retry attempt increases the timeout:

```
Attempt 1: 60s (initial_timeout)
Attempt 2: 90s (initial_timeout + 30s)
Attempt 3: 120s (initial_timeout + 60s)
Attempt N: initial_timeout + ((N-1) * 30s)
```

### Exponential Backoff

Wait time between retries doubles:

```
After attempt 1 fails: wait 1s
After attempt 2 fails: wait 2s
After attempt 3 fails: wait 4s
After attempt N fails: wait 2^(N-1) seconds
```

### Retryable Errors

The function automatically retries on these error types:

- `TimeoutError` - Request/render timeout
- `ConnectTimeout` - Connection timeout
- `ReadTimeout` - Read operation timeout
- `HTTPError` - HTTP errors (5xx server errors)
- `ConnectionError` - Network connection failures
- `ConnectError` - SSL/connection errors
- `TargetClosedError` - Playwright target closed

Non-retryable errors (e.g., `ValueError`, `ImportError`) fail immediately.

## Examples

### Handling Slow Sites

```python
# Increase timeouts for slow-loading sites
doc = retry_ingest(
    "https://slow-news-site.com",
    initial_timeout=120.0,  # Start with 2 minutes
    max_retries=3
)
```

### Bypassing Bot Detection

```python
# Enable stealth mode to bypass anti-bot measures
doc = retry_ingest(
    "https://protected-site.com",
    mode="render",
    enable_stealth=True,
    max_retries=5
)
```

### Fast Mode with Retries

```python
# Use fast mode (HTTP-only) with retries
doc = retry_ingest(
    "https://api.example.com/data",
    mode="fast",
    max_retries=3,
    initial_timeout=30.0
)
```

### Error Handling

```python
try:
    doc = retry_ingest(
        "https://unreliable-site.com",
        max_retries=5
    )
    print(f"Success after {doc.metadata['retry_attempts']} attempts")
    
except TimeoutError as e:
    print(f"All retries failed: {e}")
    
except Exception as e:
    print(f"Non-retryable error: {e}")
```

## Logging

The function logs retry attempts to stdout:

```
[MarkDownIngress] TimeoutError on attempt 1: Connection timeout
[MarkDownIngress] Waiting 1s before retry...
[MarkDownIngress] Retry attempt 2/3 for https://example.com
[MarkDownIngress] Timeout: 90.0s, Stealth: True
[MarkDownIngress] Success on attempt 2
```

## Comparison with `ingest()`

| Feature | `ingest()` | `retry_ingest()` |
|---------|-----------|------------------|
| Retry on failure | ❌ No | ✅ Yes |
| Timeout escalation | ❌ No | ✅ Yes |
| Exponential backoff | ❌ No | ✅ Yes |
| Retry metadata | ❌ No | ✅ Yes |
| Stealth mode | ❌ No | ✅ On retries |
| Use case | Simple, reliable sites | Unreliable/slow sites |

## Best Practices

1. **Start with defaults**: The default settings work well for most sites
2. **Adjust timeouts**: Increase `initial_timeout` for known slow sites
3. **Use render mode**: Combine with `mode="render"` for JavaScript-heavy sites
4. **Monitor metadata**: Check `retry_attempts` to track reliability
5. **Handle exceptions**: Always wrap in try/except for production code

## Performance Considerations

- Each retry adds latency (backoff time + request time)
- Maximum time: `(max_retries * initial_timeout) + sum(backoff_times)`
- Example: 3 retries, 60s initial = max ~7 minutes
- For batch operations, consider using `BatchProcessor` instead

## See Also

- [`ingest()`](api.md) - Standard ingestion without retries
- [`BatchProcessor`](batch.md) - Batch processing with built-in error handling
- [Configuration](config.md) - Global timeout and retry settings
