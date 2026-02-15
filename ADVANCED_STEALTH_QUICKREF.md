# Advanced Stealth Quick Reference

## 🚀 Quick Start

### Basic Usage (Async)
```python
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer

renderer = AdvancedStealthRenderer()
result = await renderer.render("https://example.com")
print(result.html)
```

### Basic Usage (Sync)
```python
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer

renderer = AdvancedStealthRenderer()
result = renderer.render_sync("https://example.com")
print(result.html)
```

## 📦 What's Included

| Component | Description | Size |
|-----------|-------------|------|
| `STEALTH_JS_INJECTION` | JavaScript to patch detection vectors | 10,603 bytes |
| `ULTRA_STEALTH_ARGS` | Browser launch arguments | 37 args |
| `ADVANCED_USER_AGENTS` | Pool of current browser UAs | 28 agents |
| `ADVANCED_VIEWPORT_SIZES` | Common viewport resolutions | 10 sizes |
| `AdvancedStealthRenderer` | Main renderer class | Full-featured |

## 🎯 Key Features

### Detection Vectors Patched (17 total)

✅ navigator.webdriver  
✅ navigator.automationControlled  
✅ Chrome runtime API  
✅ WebGL fingerprinting  
✅ Canvas fingerprinting  
✅ Plugin detection  
✅ Hardware fingerprinting  
✅ Permissions API  
✅ Automation artifacts  
✅ And 8 more...

## 🛠️ Common Use Cases

### 1. Custom Configuration
```python
from markdown_ingress.core.advanced_stealth import (
    AdvancedStealthRenderer,
    get_advanced_stealth_config,
)

config = get_advanced_stealth_config(
    randomize=True,
    viewport=(1920, 1080),
    timezone="America/New_York",
)

renderer = AdvancedStealthRenderer(stealth_config=config)
```

### 2. Cloudflare Bypass
```python
renderer = AdvancedStealthRenderer(
    timeout=60.0,  # Longer timeout
    wait_until="networkidle",
)
result = await renderer.render("https://protected-site.com")
```

### 3. Multiple URLs
```python
import asyncio

urls = ["https://site1.com", "https://site2.com"]
renderer = AdvancedStealthRenderer()

for url in urls:
    result = await renderer.render(url)
    print(f"{url}: {result.status_code}")
    await asyncio.sleep(5)  # Rate limiting
```

### 4. Manual Playwright Control
```python
from playwright.async_api import async_playwright
from markdown_ingress.core.advanced_stealth import (
    ULTRA_STEALTH_ARGS,
    get_advanced_context_options,
    inject_stealth,
)

async with async_playwright() as p:
    browser = await p.chromium.launch(
        args=ULTRA_STEALTH_ARGS,
        ignore_default_args=['--enable-automation'],
    )
    
    context = await browser.new_context(
        **get_advanced_context_options()
    )
    
    page = await context.new_page()
    await inject_stealth(page)
    await page.goto("https://example.com")
```

## ⚙️ Configuration Options

### AdvancedStealthRenderer Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `timeout` | float | 30.0 | Navigation timeout (seconds) |
| `wait_until` | str | "networkidle" | Wait strategy |
| `headless` | bool | True | Headless mode |
| `randomize_fingerprint` | bool | True | Randomize UA/viewport |
| `disable_http2` | bool | False | Disable HTTP/2 |
| `stealth_config` | Config | None | Custom config |

### Wait Strategies

- **`"networkidle"`** - Wait until network is idle (recommended)
- **`"load"`** - Wait for load event (faster)
- **`"domcontentloaded"`** - Wait for DOM ready (fastest)

## 📊 FetchResult Object

```python
result = await renderer.render(url)

# Available attributes:
result.html              # Rendered HTML
result.status_code       # HTTP status code
result.url               # Original URL
result.final_url         # Final URL after redirects
result.headers           # Response headers (dict)
result.timing_ms         # Total time in milliseconds
result.metadata          # Additional metadata (dict)
```

### Metadata Keys

- `renderer`: "advanced_stealth_playwright"
- `user_agent`: User agent used
- `viewport`: Viewport dimensions
- `device_scale_factor`: Scale factor
- `timezone`: Timezone used
- `http2_disabled`: Whether HTTP/2 was disabled
- `stealth_injected`: Always True
- `http2_fallback`: True if fell back from HTTP/2

## 🧪 Testing Sites

Test your stealth setup:

- **Bot Detection:** https://bot.sannysoft.com/
- **Headless Test:** https://arh.antoinevastel.com/bots/areyouheadless
- **Fingerprinting:** https://browserleaks.com/
- **Cloudflare:** https://nowsecure.nl

## 📝 Best Practices

### ✅ Do's

1. ✅ Use randomization for different fingerprints
2. ✅ Implement rate limiting (5-10 sec delays)
3. ✅ Use appropriate timeouts (30-60s)
4. ✅ Handle errors gracefully
5. ✅ Respect robots.txt and ToS

### ❌ Don'ts

1. ❌ Don't reuse same fingerprint for many requests
2. ❌ Don't scrape without rate limits
3. ❌ Don't ignore website policies
4. ❌ Don't use for malicious purposes
5. ❌ Don't bypass CAPTCHAs automatically

## 🐛 Troubleshooting

### Problem: Still detected as bot

**Solution:**
- Increase timeout: `timeout=60.0`
- Try different wait: `wait_until="load"`
- Use headed mode: `headless=False` (for testing)

### Problem: Slow performance

**Solution:**
- Use faster wait: `wait_until="load"`
- Reduce timeout if appropriate
- Don't disable HTTP/2 unless needed

### Problem: Cloudflare challenge

**Solution:**
- Increase timeout: `timeout=60.0`
- Some challenges can't be bypassed programmatically
- Try multiple attempts with randomization

### Problem: Import errors

**Solution:**
```bash
pip install playwright
playwright install chromium
```

## 📚 Documentation Files

- **`ADVANCED_STEALTH_GUIDE.md`** - Complete documentation
- **`ADVANCED_STEALTH_IMPLEMENTATION_SUMMARY.md`** - Implementation details
- **`examples/advanced_stealth_example.py`** - Working examples
- **`tests/test_advanced_stealth.py`** - Unit tests

## 🔗 Integration Examples

### With MarkDownIngress API
```python
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer
from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.markdown import convert_to_markdown

# Render
renderer = AdvancedStealthRenderer()
fetch_result = await renderer.render(url)

# Extract
extractor = Extractor()
extract_result = extractor.extract(
    fetch_result.html,
    fetch_result.url
)

# Convert
markdown = convert_to_markdown(extract_result.content_html)
```

### Replace Standard Renderer
```python
# Before:
from markdown_ingress.core.renderer import Renderer
renderer = Renderer(stealth=True)

# After:
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer
renderer = AdvancedStealthRenderer()
```

## 💡 Pro Tips

1. **Randomize per session, not per request** for consistency
2. **Use networkidle for SPAs**, load for static sites
3. **Test against detection sites** before production use
4. **Monitor metadata** to track HTTP/2 fallbacks
5. **Implement exponential backoff** for retries

## 📈 Performance Stats

- **Initialization:** ~500ms (browser launch)
- **Simple page:** 2-3 seconds
- **SPA/JavaScript:** 3-5 seconds
- **Cloudflare:** 10-30 seconds
- **Memory:** ~100-200MB per instance

## ⚖️ Legal & Ethical Use

This tool is for:
- ✅ Testing your own sites
- ✅ Authorized security testing
- ✅ Academic research
- ✅ Legitimate web scraping with permission
- ✅ Bot detection research

Not for:
- ❌ Bypassing paywalls
- ❌ DDoS attacks
- ❌ Stealing content
- ❌ Violating ToS
- ❌ Malicious purposes

---

**Need help?** See full documentation in `ADVANCED_STEALTH_GUIDE.md`
