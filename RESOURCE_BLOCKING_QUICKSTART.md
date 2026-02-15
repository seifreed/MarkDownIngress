# Resource Blocking - Quick Start Guide

## 🚀 What is it?

Resource Blocking speeds up page loads by **30-70%** by blocking:
- 📷 Images
- 🔤 Fonts  
- 🎥 Media (video/audio)
- 📊 Analytics & Trackers
- 🎯 Advertisements

## ⚡ Quick Start

### Enable (default behavior)
```python
from markdown_ingress.core.renderer import Renderer

# Resource blocking is ON by default
renderer = Renderer()
result = await renderer.render("https://example.com")

# Check stats
print(f"Blocked: {result.metadata['blocked_requests']} requests")
print(f"Rate: {result.metadata['block_rate_pct']:.1f}%")
```

### Disable
```python
# Turn off blocking
renderer = Renderer(block_resources=False)
```

### Custom Configuration
```python
# Only block ads/trackers, keep images
renderer = Renderer(
    block_resources=True,
    block_images=False,      # Allow images
    block_fonts=False,       # Allow fonts
    block_media=True,        # Block media
    block_ads=True,          # Block ads
    block_trackers=True      # Block trackers
)
```

## 📊 What Gets Blocked?

### Resource Types
- **Images**: JPG, PNG, GIF, SVG, WebP, etc.
- **Fonts**: WOFF, WOFF2, TTF, OTF, etc.
- **Media**: MP4, WebM, MP3, etc.
- **CSS**: Stylesheets (optional, disabled by default)

### Domains & Patterns
**Analytics** (30+ domains):
- google-analytics.com, googletagmanager.com
- segment.com, mixpanel.com, amplitude.com
- hotjar.com, mouseflow.com, clarity.ms, etc.

**Advertising**:
- doubleclick.net, googlesyndication.com
- Any URL containing: 'ads', 'adservice'

**Tracking**:
- Any URL containing: 'tracking', 'tracker', 'pixel', 'beacon', 'telemetry'

## 💡 Use Cases

### Text Extraction (recommended)
```python
# Maximum speed for markdown extraction
renderer = Renderer(
    block_resources=True,
    block_images=True,
    block_fonts=True,
    block_media=True,
    block_ads=True,
    block_trackers=True
)
```

### Complex Layouts
```python
# Keep CSS for proper layout
renderer = Renderer(
    block_resources=True,
    block_css=False  # Important!
)
```

### Privacy-Focused
```python
# Only block trackers/ads
renderer = Renderer(
    block_resources=True,
    block_images=False,
    block_fonts=False,
    block_media=False,
    block_ads=True,
    block_trackers=True
)
```

## 📈 Performance

**Typical Results**:
```
Before:  3,250ms | 120 requests
After:   1,100ms |  35 requests
Savings: 66% faster | 71% blocked
```

## 🔍 Statistics

Every render result includes blocking stats:

```python
result = await renderer.render(url)

# Available metadata
print(result.metadata['resource_blocking'])    # True
print(result.metadata['blocked_requests'])     # 85
print(result.metadata['total_requests'])       # 120
print(result.metadata['block_rate_pct'])       # 70.8
print(result.metadata['blocked_by_type'])      # {'image': 60, 'font': 12, ...}
```

## 🧪 Try It

Run the demo:
```bash
python examples/demo_resource_blocking.py
```

## 📚 Full Documentation

See [RESOURCE_BLOCKING.md](RESOURCE_BLOCKING.md) for complete documentation.

## ✅ Best Practices

1. **Always enable for text extraction** - You don't need images for markdown
2. **Keep CSS enabled** - Unless you only need raw text  
3. **Monitor block rates** - >50% is good, >70% is excellent
4. **Combine with stealth mode** - For best anti-detection
5. **Use faster wait strategies** - Try `wait_until="domcontentloaded"`

## 🎯 Examples

### Maximum Speed
```python
renderer = Renderer(
    timeout=15.0,
    wait_until="domcontentloaded",  # Fast strategy
    block_resources=True,             # Enable blocking
    stealth=True                      # Avoid detection
)
```

### Balanced
```python
renderer = Renderer(
    block_resources=True,
    block_images=True,
    block_fonts=True,
    block_media=True,
    block_css=False,      # Keep layout
    block_ads=True,
    block_trackers=True
)
```

### Debug Mode
```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Now see what's being blocked
renderer = Renderer(block_resources=True)
result = await renderer.render(url)
```

## 🚨 Troubleshooting

**Content not loading?**
```python
# Try disabling CSS blocking (it's off by default anyway)
renderer = Renderer(block_css=False)

# Or reduce blocking scope
renderer = Renderer(
    block_resources=True,
    block_images=False,  # Allow images
    block_ads=True       # Only block ads/trackers
)
```

**Want to see what's blocked?**
```python
result = await renderer.render(url)
print(result.metadata['blocked_by_type'])
# {'image': 30, 'font': 5, 'script': 10}
```

## 🎓 Advanced

### Custom Blocklist
```python
from markdown_ingress.core.resource_blocker import ResourceBlocker

blocker = ResourceBlocker(
    custom_blocked_domains=[
        'custom-tracker.com',
        'slow-cdn.net',
        'unwanted-widgets.io'
    ]
)

# Use with Playwright page
await blocker.setup_blocking(page)
```

### Statistics
```python
stats = blocker.get_stats()
print(stats)
# {
#   'blocked_requests': 42,
#   'total_requests': 100,
#   'block_rate_pct': 42.0,
#   'blocked_by_type': {...},
#   'blocked_by_domain': {...}
# }
```

## 📦 What You Get

- ✅ 30-70% faster page loads
- ✅ 50-90% less bandwidth
- ✅ Built-in ad/tracker blocking
- ✅ Detailed statistics
- ✅ Zero configuration (works out of the box)
- ✅ Fully customizable
- ✅ No breaking changes

---

**Ready to go faster?** Resource blocking is enabled by default! 🚀
