# Advanced Stealth Mode Documentation

## Overview

The `advanced_stealth.py` module provides comprehensive stealth capabilities for bypassing sophisticated bot detection systems including:

- **Cloudflare** bot detection
- **Browser fingerprinting** (Canvas, WebGL, Audio)
- **WebDriver detection** (navigator.webdriver, automation flags)
- **Behavioral analysis** systems
- **TLS fingerprinting** evasion

## Key Features

### 1. Comprehensive JavaScript Injection

The module includes extensive JavaScript patches that override all known detection vectors:

```python
from markdown_ingress.core.advanced_stealth import STEALTH_JS_INJECTION

# Over 400 lines of stealth JavaScript that patches:
# - navigator.webdriver
# - Chrome runtime API
# - Permissions API
# - WebGL fingerprinting
# - Canvas fingerprinting
# - Plugin detection
# - And much more...
```

### 2. Ultra Stealth Browser Arguments

60+ Chromium command-line arguments specifically designed to hide automation:

```python
from markdown_ingress.core.advanced_stealth import ULTRA_STEALTH_ARGS

# Includes critical flags like:
# --disable-blink-features=AutomationControlled
# --disable-features=IsolateOrigins,site-per-process
# And many more...
```

### 3. Advanced Browser Context Options

Randomized, realistic browser fingerprints:

```python
from markdown_ingress.core.advanced_stealth import get_advanced_context_options

options = get_advanced_context_options()
# Returns:
# - Randomized user agent from pool of 30+ current browsers
# - Randomized viewport from 10+ common resolutions
# - Device scale factor (1.0-2.0)
# - Realistic HTTP headers
# - Timezone randomization
# - And more...
```

## Quick Start

### Basic Usage

```python
import asyncio
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer

async def main():
    # Create renderer with default advanced stealth
    renderer = AdvancedStealthRenderer()
    
    # Render a protected page
    result = await renderer.render("https://example.com")
    
    print(f"Status: {result.status_code}")
    print(f"HTML length: {len(result.html)}")
    print(f"Timing: {result.timing_ms}ms")

asyncio.run(main())
```

### Synchronous Usage

```python
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer

renderer = AdvancedStealthRenderer()
result = renderer.render_sync("https://example.com")
print(result.html)
```

### Custom Configuration

```python
from markdown_ingress.core.advanced_stealth import (
    AdvancedStealthRenderer,
    get_advanced_stealth_config,
)

# Create custom stealth configuration
config = get_advanced_stealth_config(
    randomize=True,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...",
    viewport=(1920, 1080),
    timezone="America/New_York",
)

# Use custom config
renderer = AdvancedStealthRenderer(
    timeout=30.0,
    headless=True,
    stealth_config=config,
)

result = await renderer.render("https://example.com")
```

## AdvancedStealthRenderer API

### Constructor Parameters

```python
AdvancedStealthRenderer(
    timeout: float = 30.0,
    wait_until: str = "networkidle",
    headless: bool = True,
    randomize_fingerprint: bool = True,
    disable_http2: bool = False,
    stealth_config: Optional[AdvancedStealthConfig] = None,
)
```

**Parameters:**

- **timeout** (float): Navigation timeout in seconds. Default: 30.0
- **wait_until** (str): When to consider navigation complete. Options:
  - `"networkidle"` - Wait until network is idle (recommended)
  - `"load"` - Wait for load event
  - `"domcontentloaded"` - Wait for DOMContentLoaded event
- **headless** (bool): Run browser in headless mode. Default: True
- **randomize_fingerprint** (bool): Randomize user agent, viewport, etc. Default: True
- **disable_http2** (bool): Disable HTTP/2 protocol. Default: False
- **stealth_config** (AdvancedStealthConfig, optional): Custom stealth configuration

### Methods

#### `async render(url: str) -> FetchResult`

Render a URL with advanced stealth techniques.

**Returns:** `FetchResult` object with:
- `html`: Rendered HTML content
- `status_code`: HTTP status code
- `final_url`: Final URL after redirects
- `headers`: Response headers
- `timing_ms`: Total time in milliseconds
- `metadata`: Additional metadata including stealth settings

**Example:**
```python
result = await renderer.render("https://example.com")
print(result.html)
print(result.metadata['user_agent'])
```

#### `render_sync(url: str) -> FetchResult`

Synchronous wrapper for `render()`.

**Example:**
```python
result = renderer.render_sync("https://example.com")
```

## Configuration Classes

### AdvancedStealthConfig

Comprehensive stealth configuration dataclass.

**Fields:**

```python
@dataclass
class AdvancedStealthConfig:
    user_agent: str
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    locale: str = "en-US"
    timezone: str = "America/New_York"
    permissions: list[str] = ["geolocation", "notifications"]
    extra_http_headers: dict[str, str] = {}
    browser_args: list[str] = []
    enable_javascript: bool = True
    bypass_csp: bool = True
    ignore_https_errors: bool = True
    has_touch: bool = False
    is_mobile: bool = False
    geolocation: Optional[dict] = None
```

## Helper Functions

### `get_advanced_stealth_config()`

Get a randomized advanced stealth configuration.

```python
def get_advanced_stealth_config(
    randomize: bool = True,
    user_agent: Optional[str] = None,
    viewport: Optional[tuple[int, int]] = None,
    timezone: Optional[str] = None,
) -> AdvancedStealthConfig
```

**Example:**
```python
config = get_advanced_stealth_config(randomize=True)
print(config.user_agent)
print(f"{config.viewport_width}x{config.viewport_height}")
```

### `get_advanced_context_options()`

Get Playwright context options with all anti-detection measures.

```python
def get_advanced_context_options(
    stealth_config: Optional[AdvancedStealthConfig] = None,
) -> dict[str, Any]
```

**Example:**
```python
options = get_advanced_context_options()
context = await browser.new_context(**options)
```

### `async inject_stealth(page)`

Inject stealth scripts into a Playwright page.

```python
async def inject_stealth(page) -> None
```

**Example:**
```python
page = await context.new_page()
await inject_stealth(page)
await page.goto("https://example.com")
```

## Manual Playwright Integration

For advanced users who want full control:

```python
from playwright.async_api import async_playwright
from markdown_ingress.core.advanced_stealth import (
    ULTRA_STEALTH_ARGS,
    get_advanced_context_options,
    inject_stealth,
)

async with async_playwright() as p:
    # Launch with ultra stealth arguments
    browser = await p.chromium.launch(
        headless=True,
        args=ULTRA_STEALTH_ARGS,
        ignore_default_args=['--enable-automation'],
    )
    
    # Create context with advanced options
    context_options = get_advanced_context_options()
    context = await browser.new_context(**context_options)
    
    # Create page and inject stealth
    page = await context.new_page()
    await inject_stealth(page)
    
    # Now use the page normally
    await page.goto("https://example.com")
    html = await page.content()
    
    await context.close()
    await browser.close()
```

## Constants and Pools

### User Agent Pool

```python
ADVANCED_USER_AGENTS  # 30+ current browser user agents
```

Includes:
- Chrome 120-123 (Windows, macOS, Linux)
- Edge 120-123 (Windows, macOS)
- Firefox 121-124 (Windows, macOS, Linux)
- Safari 17+ (macOS)

### Viewport Pool

```python
ADVANCED_VIEWPORT_SIZES  # 10 common viewport sizes
```

Includes resolutions from 1280x720 to 2560x1440.

### Timezone Pool

```python
TIMEZONES  # 10 common timezones
```

Covers major regions: US, Europe, Asia, Australia.

### HTTP Headers

```python
REALISTIC_HEADERS  # Realistic browser HTTP headers
```

Includes all modern browser headers like `Sec-Ch-Ua`, `Sec-Fetch-*`, etc.

## Detection Vectors Patched

The stealth JavaScript injection patches the following detection vectors:

### Core WebDriver Detection
- ✅ `navigator.webdriver`
- ✅ `navigator.automationControlled`
- ✅ Chrome DevTools Protocol artifacts

### Browser APIs
- ✅ `window.chrome.runtime`
- ✅ Permissions API
- ✅ Notification API
- ✅ Battery API (removed)
- ✅ Connection API (standardized)

### Fingerprinting
- ✅ WebGL vendor/renderer
- ✅ Canvas fingerprinting (noise added)
- ✅ Plugin enumeration
- ✅ Hardware concurrency
- ✅ Device memory
- ✅ Screen resolution consistency

### Automation Artifacts
- ✅ `window.cdc_*` properties
- ✅ `__webdriver_*` properties
- ✅ `__selenium_*` properties
- ✅ Error stack trace cleaning
- ✅ Console debug filtering

### Mouse & Touch Events
- ✅ Touch event consistency
- ✅ Mouse event consistency

### Frame Detection
- ✅ `window.top` consistency
- ✅ `window.frameElement` patching

## Best Practices

### 1. Use Randomization

Always enable randomization to avoid fingerprint-based blocking:

```python
renderer = AdvancedStealthRenderer(randomize_fingerprint=True)
```

### 2. Adjust Timeouts

For heavily protected sites, increase timeout:

```python
renderer = AdvancedStealthRenderer(timeout=60.0)  # 60 seconds
```

### 3. Handle HTTP/2 Errors

The renderer automatically retries with HTTP/2 disabled on protocol errors:

```python
result = await renderer.render(url)
if result.metadata.get('http2_fallback'):
    print("Fell back to HTTP/1.1")
```

### 4. Wait Strategy

Choose appropriate wait strategy:
- **networkidle** - Best for SPAs and dynamic content (default)
- **load** - Faster but may miss dynamic content
- **domcontentloaded** - Fastest but minimal JS execution

### 5. Headless vs Headed

For maximum stealth, use headless mode. Some sites detect headed browsers:

```python
renderer = AdvancedStealthRenderer(headless=True)  # Recommended
```

### 6. Rate Limiting

Add delays between requests to avoid rate limiting:

```python
import asyncio

urls = ["https://example1.com", "https://example2.com"]
for url in urls:
    result = await renderer.render(url)
    await asyncio.sleep(5)  # 5 second delay
```

## Testing Against Bot Detection

### Test Sites

Test your stealth setup against these bot detection services:

1. **Bot Detection Tests:**
   - https://bot.sannysoft.com/
   - https://arh.antoinevastel.com/bots/areyouheadless
   - https://pixelscan.net/

2. **Cloudflare Protected:**
   - https://nowsecure.nl
   - Various Cloudflare-protected sites

3. **Fingerprinting Tests:**
   - https://browserleaks.com/
   - https://amiunique.org/

### Example Test

```python
async def test_bot_detection():
    renderer = AdvancedStealthRenderer()
    
    # Test against bot detection service
    result = await renderer.render("https://bot.sannysoft.com/")
    
    # Check for detection indicators
    html = result.html.lower()
    if 'webdriver' in html and 'true' in html:
        print("❌ WebDriver detected")
    else:
        print("✅ WebDriver NOT detected")
```

## Integration with MarkDownIngress

### Option 1: Use AdvancedStealthRenderer Directly

```python
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer
from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.markdown import convert_to_markdown

# Render with advanced stealth
renderer = AdvancedStealthRenderer()
fetch_result = await renderer.render("https://example.com")

# Extract content
extractor = Extractor()
extract_result = extractor.extract(fetch_result.html, fetch_result.url)

# Convert to markdown
markdown = convert_to_markdown(extract_result.content_html)
```

### Option 2: Replace Standard Renderer

```python
# In your code that uses markdown_ingress
from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer

# Instead of:
# from markdown_ingress.core.renderer import Renderer
# renderer = Renderer(stealth=True)

# Use:
renderer = AdvancedStealthRenderer()
```

## Troubleshooting

### Issue: Still Getting Detected

**Solutions:**
1. Increase timeout: `AdvancedStealthRenderer(timeout=60.0)`
2. Try different wait strategy: `wait_until="load"`
3. Disable HTTP/2: `disable_http2=True`
4. Use headed mode for testing: `headless=False`

### Issue: Slow Performance

**Solutions:**
1. Use `wait_until="load"` instead of "networkidle"
2. Reduce timeout if not needed
3. Use HTTP/2 (don't disable it)

### Issue: Cloudflare Challenge Not Bypassing

**Solutions:**
1. Increase timeout to 60+ seconds
2. Some challenges require user interaction (not bypassable)
3. Try multiple times with different fingerprints
4. Consider using residential proxies (not included)

### Issue: Import Errors

**Solution:**
```bash
# Install playwright
pip install playwright
playwright install

# Or install with render extra
pip install 'markdown-ingress[render]'
```

## Performance Considerations

### Memory Usage

- Each browser instance uses ~100-200MB RAM
- Close browsers when done
- Use context managers (`async with`)

### Speed

- Average render time: 2-5 seconds
- Cloudflare bypass: 10-30 seconds
- Heavy SPAs: 5-15 seconds

### Concurrency

```python
# Run multiple renders concurrently
async def render_multiple(urls):
    renderer = AdvancedStealthRenderer()
    tasks = [renderer.render(url) for url in urls]
    results = await asyncio.gather(*tasks)
    return results
```

## Security Notes

⚠️ **Important:** This module is designed for legitimate web scraping and testing purposes. Always:

1. Respect `robots.txt`
2. Follow website Terms of Service
3. Implement rate limiting
4. Identify your bot with appropriate User-Agent when not testing
5. Don't use for malicious purposes

## License

This module is part of MarkDownIngress and follows the same license.

## Support

For issues, questions, or contributions:
- GitHub Issues: [MarkDownIngress Issues](https://github.com/yourusername/MarkDownIngress/issues)
- Documentation: See main README.md

## Changelog

### Version 1.0.0 (Current)

- ✅ Comprehensive JavaScript injection (400+ lines)
- ✅ 60+ ultra stealth browser arguments
- ✅ Advanced browser context options
- ✅ Randomized fingerprints
- ✅ HTTP/2 fallback support
- ✅ AdvancedStealthRenderer class
- ✅ Full Playwright integration
- ✅ Extensive documentation and examples
