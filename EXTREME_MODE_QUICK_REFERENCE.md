# Extreme Mode - Quick Reference

## When to Use Extreme Mode

Use `extreme_mode=True` for sites that:
- Take 2-5 minutes to load
- Have heavy JavaScript frameworks (React/Vue/Angular with SSR)
- Use aggressive bot protection (Cloudflare, etc.)
- Have complex SPAs with delayed content loading
- Timeout with normal mode (30-60s)

## Quick Start

### Option 1: Direct Usage
```python
from markdown_ingress import ingest

doc = ingest(
    url="https://very-slow-site.com",
    mode="render",
    extreme_mode=True,  # Enable extreme patience
    timeout=300.0       # 5 minutes max
)
```

### Option 2: Automatic (Recommended)
```python
from markdown_ingress import retry_ingest

# Extreme mode auto-enabled on last attempt
doc = retry_ingest(
    url="https://protected-site.com",
    mode="render",
    max_retries=3,
    enable_stealth=True
)
```

## Progressive Strategies

When `extreme_mode=True`, the system tries 3 strategies:

| Attempt | Wait State         | Timeout | When It Works                          |
|---------|-------------------|---------|----------------------------------------|
| 1       | `networkidle`     | 90s     | Network becomes idle (no requests)     |
| 2       | `domcontentloaded`| 180s    | DOM fully parsed                       |
| 3       | `load`            | 300s    | Complete page load (images, styles)    |

## Check Which Strategy Worked

```python
doc = ingest(url, mode="render", extreme_mode=True)

if doc.metadata.get('extreme_mode'):
    print(f"Strategy: {doc.metadata['strategy_used']}")
    print(f"Attempt: {doc.metadata['strategy_attempt']}/3")
    print(f"Timeout: {doc.metadata['timeout_used_ms']/1000}s")
```

## Smart Content Waiting

Extreme mode also uses smart waiting:

1. **Content Selectors** - Tries in order:
   - `article`
   - `main`
   - `[role="main"]`
   - `.content`
   - `#content`
   - `body`

2. **Content Verification**:
   - Body has >50 characters of text
   - At least one content element exists
   - Loading indicators are hidden/gone

## Combine with Other Features

### Maximum Protection Stack
```python
doc = retry_ingest(
    url="https://heavily-protected-site.com",
    mode="render",
    max_retries=5,        # More attempts
    enable_stealth=True,  # Stealth mode
    initial_timeout=90.0  # Higher initial timeout
)
# Final attempt will use:
# - timeout=210s (90 + 4*30)
# - stealth=True
# - extreme_mode=True (auto-enabled)
#   → Then progressive: 90s → 180s → 300s
```

### Manual Fallback
```python
try:
    # Try normal first
    doc = ingest(url, mode="render", timeout=30.0)
except Exception:
    # Fallback to extreme
    doc = ingest(url, mode="render", extreme_mode=True, timeout=300.0)
```

## Metadata Available

| Field | Description |
|-------|-------------|
| `extreme_mode` | Whether extreme mode was used (True/False) |
| `strategy_used` | Which wait state worked ('networkidle', 'domcontentloaded', 'load') |
| `strategy_attempt` | Which attempt succeeded (1, 2, or 3) |
| `timeout_used_ms` | Actual timeout used (90000, 180000, or 300000) |
| `smart_wait_used` | Whether smart content waiting was used (True/False) |
| `retry_attempts` | Number of retry attempts made (from retry_ingest) |
| `extreme_mode_enabled` | Whether extreme mode was enabled (from retry_ingest) |

## Performance Considerations

- **Normal Mode**: No impact, same as before
- **Extreme Mode**: Only use when necessary
  - Adds 90-300 seconds to load time
  - Best for <1% of sites that are extremely slow
  - Trades speed for reliability

## Common Patterns

### Pattern 1: One-shot for Known Slow Site
```python
doc = ingest(url, mode="render", extreme_mode=True, timeout=300.0)
```

### Pattern 2: Auto-escalating Retry (Recommended)
```python
doc = retry_ingest(url, mode="render", max_retries=3, enable_stealth=True)
```

### Pattern 3: Conditional Based on Previous Failure
```python
if previous_attempt_failed:
    doc = ingest(url, mode="render", extreme_mode=True, stealth=True)
else:
    doc = ingest(url, mode="render")
```

## Troubleshooting

### Still Timing Out?
- Increase `timeout` parameter beyond 300s
- Check site is actually accessible
- Verify network connectivity

### Getting Blocked?
- Enable `stealth=True`
- Use `retry_ingest()` for automatic escalation
- Check user agent settings

### No Content Returned?
- Site may use non-standard loading pattern
- Check `doc.metadata['strategy_used']` to see what worked
- Try different `wait_until` strategies manually

## Example: Debugging Slow Site

```python
import logging
logging.basicConfig(level=logging.INFO)

from markdown_ingress import ingest

doc = ingest(
    url="https://problem-site.com",
    mode="render",
    extreme_mode=True,
    timeout=300.0,
    stealth=True
)

# Check logs for:
# - [Extreme Mode] Attempt 1/3: networkidle (90s)
# - [Smart Wait] Found content selector: article
# - [Smart Wait] Content verification passed
# - [Extreme Mode] Success with networkidle strategy

print(f"\nSuccess!")
print(f"Strategy: {doc.metadata.get('strategy_used')}")
print(f"Tokens: {doc.token_estimate}")
```

## See Also

- `examples/extreme_mode_example.py` - 5 detailed examples
- `EXTREME_MODE_IMPLEMENTATION.md` - Complete implementation details
- `RETRY_DOCUMENTATION.md` - Retry strategy guide
- `HTTP2_STEALTH_GUIDE.md` - Stealth mode guide
