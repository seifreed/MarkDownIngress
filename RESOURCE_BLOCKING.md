# Resource Blocking Documentation

## Overview

The Resource Blocker is a performance optimization feature that intercepts and blocks unnecessary network requests during page rendering. This significantly speeds up page loads by preventing the download of images, fonts, media, ads, and tracking scripts.

## Features

### Blockable Resource Types

1. **Images** (`image`) - Block all image requests (JPG, PNG, GIF, etc.)
2. **Fonts** (`font`) - Block web font downloads (WOFF, TTF, etc.)
3. **Media** (`media`) - Block video and audio files
4. **Stylesheets** (`stylesheet`) - Optionally block CSS files (may break layout)

### Domain-Based Blocking

The blocker also blocks requests to known advertising and tracking domains:

- **Analytics**: Google Analytics, Google Tag Manager, Segment, Mixpanel, etc.
- **Advertising**: DoubleClick, Google Syndication, ad networks
- **Tracking**: Tracking scripts, pixels, beacons, telemetry
- **Social Trackers**: Facebook Pixel, Twitter tracking

## Usage

### Basic Usage with Renderer

```python
from markdown_ingress.core.renderer import Renderer

# Create renderer with resource blocking enabled (default)
renderer = Renderer(
    timeout=30.0,
    block_resources=True,      # Enable blocking (default: True)
    block_images=True,         # Block images (default: True)
    block_fonts=True,          # Block fonts (default: True)
    block_media=True,          # Block media (default: True)
    block_ads=True,            # Block ads (default: True)
    block_trackers=True        # Block trackers (default: True)
)

# Render page with blocking
result = await renderer.render("https://example.com")

# Check blocking statistics
print(f"Blocked: {result.metadata['blocked_requests']} requests")
print(f"Total: {result.metadata['total_requests']} requests")
print(f"Block rate: {result.metadata['block_rate_pct']:.1f}%")
```

### Direct ResourceBlocker Usage

```python
from markdown_ingress.core.resource_blocker import ResourceBlocker
from playwright.async_api import async_playwright

async def custom_blocking():
    # Create blocker with custom settings
    blocker = ResourceBlocker(
        block_images=True,
        block_fonts=True,
        block_media=True,
        block_css=False,  # Don't block CSS (keep layout)
        block_ads=True,
        block_trackers=True,
        custom_blocked_domains=['custom-tracker.com', 'unwanted-cdn.net']
    )
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        # Setup blocking on page
        await blocker.setup_blocking(page)
        
        # Navigate (blocking is active)
        await page.goto("https://example.com")
        
        # Get statistics
        stats = blocker.get_stats()
        print(f"Blocked {stats['blocked_requests']} requests")
        print(f"Blocked by type: {stats['blocked_by_type']}")
        
        await browser.close()
```

### Disable Resource Blocking

```python
# Disable all blocking
renderer = Renderer(block_resources=False)

# Or selectively disable specific types
renderer = Renderer(
    block_resources=True,
    block_images=False,    # Allow images
    block_fonts=False,     # Allow fonts
    block_media=True,      # Block media
    block_ads=True,        # Block ads
    block_trackers=True    # Block trackers
)
```

## Configuration Options

### ResourceBlocker Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `block_images` | bool | `True` | Block image requests |
| `block_fonts` | bool | `True` | Block font requests |
| `block_media` | bool | `True` | Block media (video/audio) |
| `block_css` | bool | `False` | Block stylesheets (may break layout) |
| `block_ads` | bool | `True` | Block advertising domains |
| `block_trackers` | bool | `True` | Block analytics/tracking domains |
| `custom_blocked_domains` | List[str] | `None` | Additional domains to block |

### Renderer Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `block_resources` | bool | `True` | Enable/disable resource blocking |
| `block_images` | bool | `True` | Block images when blocking enabled |
| `block_fonts` | bool | `True` | Block fonts when blocking enabled |
| `block_media` | bool | `True` | Block media when blocking enabled |
| `block_ads` | bool | `True` | Block ads when blocking enabled |
| `block_trackers` | bool | `True` | Block trackers when blocking enabled |

## Statistics and Monitoring

The blocker tracks detailed statistics about blocked requests:

```python
# Get stats from blocker
stats = blocker.get_stats()

print(stats)
# {
#     'blocked_requests': 42,
#     'total_requests': 100,
#     'allowed_requests': 58,
#     'block_rate_pct': 42.0,
#     'blocked_by_type': {
#         'image': 30,
#         'font': 5,
#         'media': 2
#     },
#     'blocked_by_domain': {
#         'google-analytics.com': 3,
#         'doubleclick.net': 2
#     }
# }
```

### Metadata in FetchResult

When using the Renderer, blocking statistics are automatically included in the result metadata:

```python
result = await renderer.render(url)

# Blocking metadata
if result.metadata.get('resource_blocking'):
    print(f"Blocked: {result.metadata['blocked_requests']}")
    print(f"Total: {result.metadata['total_requests']}")
    print(f"Rate: {result.metadata['block_rate_pct']}%")
    print(f"By type: {result.metadata['blocked_by_type']}")
```

## Performance Impact

### Typical Results

Resource blocking can significantly improve performance:

- **Speed improvement**: 30-70% faster page loads
- **Bandwidth reduction**: 50-90% less data transferred
- **Block rate**: 40-80% of requests blocked (depends on site)

### Example Comparison

```
Without blocking:
- Time: 3,250ms
- Requests: 120
- HTML size: 450KB

With blocking:
- Time: 1,100ms (66% faster)
- Requests: 35 (71% blocked)
- HTML size: 450KB (same - only text content)
- Blocked: 85 requests (71% block rate)
  - Images: 60
  - Fonts: 12
  - Ads/Trackers: 13
```

## Blocked Domains List

The blocker includes a comprehensive list of known ad and tracking domains:

### Analytics & Tracking
- google-analytics.com
- googletagmanager.com
- segment.com
- mixpanel.com
- amplitude.com
- hotjar.com
- mouseflow.com
- fullstory.com
- clarity.ms

### Advertising
- doubleclick.net
- googlesyndication.com
- adservice.google
- facebook.net
- scorecardresearch.com
- quantserve.com

### Pattern Matching
URLs containing these patterns are also blocked:
- 'ads'
- 'analytics'
- 'tracking'
- 'tracker'
- 'pixel'
- 'beacon'
- 'telemetry'

## Custom Blocking

### Add Custom Domains

```python
blocker = ResourceBlocker(
    custom_blocked_domains=[
        'unwanted-cdn.com',
        'slow-service.net',
        'third-party-widgets.io'
    ]
)
```

### Advanced Filtering

For more complex blocking logic, you can extend the ResourceBlocker:

```python
from markdown_ingress.core.resource_blocker import ResourceBlocker

class CustomBlocker(ResourceBlocker):
    def _should_block(self, resource_type: str, url: str) -> bool:
        # Call parent logic first
        if super()._should_block(resource_type, url):
            return True
        
        # Add custom logic
        if 'expensive-api.com' in url and resource_type == 'xhr':
            return True
        
        # Block large files
        if url.endswith('.mp4') or url.endswith('.webm'):
            return True
        
        return False
```

## Best Practices

### 1. Always Enable for Text Extraction

When extracting text/markdown from pages, always enable resource blocking:

```python
renderer = Renderer(
    block_resources=True,
    block_images=True,
    block_fonts=True,
    block_media=True
)
```

### 2. Don't Block CSS for Complex Sites

For sites with dynamic layouts, keep CSS enabled:

```python
renderer = Renderer(
    block_resources=True,
    block_css=False  # Keep CSS for proper layout
)
```

### 3. Monitor Block Rates

High block rates indicate good optimization:

```python
result = await renderer.render(url)
block_rate = result.metadata.get('block_rate_pct', 0)

if block_rate > 50:
    print("✓ Good blocking rate")
elif block_rate < 20:
    print("⚠ Low blocking rate - may need more aggressive settings")
```

### 4. Use with Stealth Mode

Combine with stealth mode for best results:

```python
renderer = Renderer(
    stealth=True,           # Avoid detection
    block_resources=True,   # Speed up loads
    block_ads=True,         # Block ads
    block_trackers=True     # Block trackers
)
```

## Troubleshooting

### Content Not Loading

If content doesn't appear:

1. Check if CSS is being blocked:
   ```python
   renderer = Renderer(block_css=False)
   ```

2. Try allowing images:
   ```python
   renderer = Renderer(block_images=False)
   ```

3. Reduce blocking scope:
   ```python
   renderer = Renderer(
       block_resources=True,
       block_ads=True,      # Only block ads/trackers
       block_trackers=True,
       block_images=False,  # Allow content resources
       block_fonts=False,
       block_media=False
   )
   ```

### Debugging Blocked Requests

Enable logging to see what's being blocked:

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('markdown_ingress.core.resource_blocker')

# Now you'll see debug output like:
# DEBUG:markdown_ingress.core.resource_blocker:Blocked image: https://example.com/photo.jpg
# DEBUG:markdown_ingress.core.resource_blocker:Blocked font: https://fonts.googleapis.com/...
```

## API Reference

### ResourceBlocker Class

```python
class ResourceBlocker:
    def __init__(
        self,
        block_images: bool = True,
        block_fonts: bool = True,
        block_media: bool = True,
        block_css: bool = False,
        block_ads: bool = True,
        block_trackers: bool = True,
        custom_blocked_domains: Optional[List[str]] = None
    )
    
    async def setup_blocking(self, page) -> None
    def get_stats(self) -> dict
    def reset_stats(self) -> None
```

### Methods

#### `setup_blocking(page)`
Setup request interception on a Playwright page.

**Parameters:**
- `page`: Playwright Page object

**Returns:** None

#### `get_stats()`
Get blocking statistics.

**Returns:** Dictionary with:
- `blocked_requests`: Number of blocked requests
- `total_requests`: Total number of requests
- `allowed_requests`: Number of allowed requests
- `block_rate_pct`: Percentage of blocked requests
- `blocked_by_type`: Dict of blocked counts by resource type
- `blocked_by_domain`: Dict of blocked counts by domain pattern

#### `reset_stats()`
Reset all statistics to zero.

**Returns:** None

## Examples

See the `examples/demo_resource_blocking.py` script for a complete demonstration.

```bash
# Run the demo
python examples/demo_resource_blocking.py
```

## Performance Tips

1. **Always enable for markdown extraction** - You don't need images/fonts for text
2. **Combine with faster wait strategies** - Use `wait_until="domcontentloaded"` instead of `"networkidle"`
3. **Monitor statistics** - Check block rates to optimize settings
4. **Use custom domains** - Add site-specific blocklists for better results
5. **Keep CSS enabled** - Unless you only need raw text

## Future Enhancements

Potential improvements:

- Request size limits (block requests > X MB)
- Smart image blocking (allow small logos, block large images)
- Request priority scoring
- Allowlist support (never block certain domains)
- Request caching integration
- Bandwidth usage tracking
