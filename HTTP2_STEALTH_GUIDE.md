# HTTP/2 Protocol Error Handling and Stealth Mode

## Overview

MarkDownIngress renderer now includes automatic HTTP/2 fallback and stealth mode support to handle protocol errors and avoid bot detection.

## Features

### 1. HTTP/2 Automatic Fallback

When a website encounters `ERR_HTTP2_PROTOCOL_ERROR`, the renderer automatically retries with HTTP/2 disabled:

```python
from markdown_ingress.core.renderer import Renderer

renderer = Renderer(timeout=30.0)
result = await renderer.render("https://example.com")

# Check if fallback was used
if result.metadata.get('http2_fallback'):
    print("HTTP/2 fallback was triggered")
    print(f"Original error: {result.metadata.get('original_error')}")
```

### 2. Stealth Mode

Enable stealth mode to avoid bot detection mechanisms:

```python
renderer = Renderer(
    timeout=30.0,
    stealth=True  # Enable stealth mode
)
result = await renderer.render("https://example.com")

# Stealth mode includes:
# - Randomized user agents
# - Randomized viewport sizes
# - Disabled automation indicators
# - Proper browser fingerprinting
```

### 3. Manual HTTP/2 Control

Manually disable HTTP/2 if needed:

```python
renderer = Renderer(
    timeout=30.0,
    disable_http2=True  # Force HTTP/1.1
)
result = await renderer.render("https://example.com")
```

### 4. Combined Options

Use stealth mode with HTTP/2 fallback:

```python
renderer = Renderer(
    timeout=30.0,
    stealth=True,
    headless=True,
    user_agent="Custom User Agent"  # Optional override
)
result = await renderer.render("https://problematic-site.com")

# Check metadata
print(f"Renderer: {result.metadata['renderer']}")
print(f"Stealth mode: {result.metadata['stealth_mode']}")
print(f"HTTP/2 disabled: {result.metadata['http2_disabled']}")
print(f"HTTP/2 fallback triggered: {result.metadata.get('http2_fallback', False)}")
```

## Metadata Fields

The `FetchResult.metadata` dictionary now includes:

- `renderer`: Always "playwright"
- `stealth_mode`: Boolean indicating if stealth was enabled
- `http2_disabled`: Boolean indicating if HTTP/2 was disabled
- `http2_fallback`: Boolean (only present if fallback was triggered)
- `original_error`: String (only present if fallback was triggered)

## Example: Full Integration with MarkDownIngress

```python
import asyncio
from markdown_ingress import ingest

async def process_protected_site():
    """Process a site that may have bot protection or HTTP/2 issues"""
    
    result = await ingest(
        "https://protected-site.com",
        render=True,  # Enable JavaScript rendering
        stealth=True,  # Enable stealth mode
        timeout=60.0
    )
    
    print(f"Markdown length: {len(result.markdown)}")
    print(f"Injection score: {result.injection_score}")
    print(f"Flags: {result.flags}")
    
    # Check if HTTP/2 fallback was used
    if 'http2_fallback' in result.metadata:
        print("⚠️  HTTP/2 fallback was triggered")

if __name__ == "__main__":
    asyncio.run(process_protected_site())
```

## Error Handling

The renderer will:

1. Try the request with configured options
2. If `ERR_HTTP2_PROTOCOL_ERROR` occurs and `disable_http2=False`:
   - Automatically retry with `--disable-http2` flag
   - Mark result with `http2_fallback: True`
3. Other errors are raised normally

```python
try:
    result = await renderer.render("https://example.com")
except Exception as e:
    if 'Timeout' in str(e):
        print("Navigation timeout - site took too long")
    elif 'net::' in str(e):
        print("Network error occurred")
    else:
        print(f"Rendering failed: {e}")
```

## Performance Considerations

- **Stealth mode**: Adds minimal overhead (randomization only)
- **HTTP/2 fallback**: Only triggers on error, adds one retry attempt
- **Recommended**: Start without stealth, enable if needed

## Testing

Run the test suite to verify functionality:

```bash
python test_http2_fallback.py
```

## Browser Arguments

### Standard Mode
- Basic Chromium launch with minimal arguments

### Stealth Mode
Includes comprehensive stealth arguments:
- `--disable-blink-features=AutomationControlled`
- `--no-sandbox`
- `--disable-dev-shm-usage`
- And many more (see `core/stealth.py`)

### HTTP/2 Disabled
- Adds `--disable-http2` flag to force HTTP/1.1

## Advanced Usage

### Custom Stealth Configuration

```python
from markdown_ingress.core.stealth import get_stealth_config, get_context_options

# Get default stealth config (randomized)
config = get_stealth_config()
print(f"Using user agent: {config.user_agent}")
print(f"Viewport: {config.viewport_width}x{config.viewport_height}")

# Get context options
options = get_context_options(config)
```

### Retry Strategy

```python
async def robust_render(url: str, max_retries: int = 3):
    """Render with multiple fallback strategies"""
    
    strategies = [
        {"stealth": False, "disable_http2": False},
        {"stealth": True, "disable_http2": False},
        {"stealth": True, "disable_http2": True},
    ]
    
    for i, strategy in enumerate(strategies):
        try:
            renderer = Renderer(timeout=30.0, **strategy)
            result = await renderer.render(url)
            print(f"Success with strategy {i+1}: {strategy}")
            return result
        except Exception as e:
            if i < len(strategies) - 1:
                print(f"Strategy {i+1} failed: {e}, trying next...")
            else:
                raise
```

## Troubleshooting

### Still Getting Blocked?

1. **Increase timeout**: Some sites need more time
2. **Enable stealth**: Use `stealth=True`
3. **Custom user agent**: Provide realistic user agent
4. **Disable HTTP/2**: Try `disable_http2=True`

### HTTP/2 Errors Persist?

If automatic fallback doesn't work:
- Check if site requires HTTP/2 (rare)
- Verify Playwright/browser version
- Try updating: `playwright install chromium`

### Performance Issues?

- Start headless: `headless=True` (default)
- Reduce timeout: `timeout=10.0`
- Disable stealth if not needed

## Summary

The HTTP/2 fallback and stealth mode features make MarkDownIngress more robust:

✅ Automatic recovery from HTTP/2 protocol errors  
✅ Bot detection avoidance with stealth mode  
✅ Comprehensive metadata tracking  
✅ Zero breaking changes to existing code  
✅ Production-ready and well-tested
