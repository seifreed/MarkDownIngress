# Development Guide

Guide for developers working on MarkDownIngress.

## Architecture Overview

MarkDownIngress follows a modular architecture with clean separation of concerns:

```
markdown_ingress/
├── core/               # Core processing modules
│   ├── fetcher.py     # HTTP/async fetching
│   ├── renderer.py    # Playwright browser rendering
│   ├── extractor.py   # Content extraction (Readability)
│   ├── normalizer.py  # Text normalization
│   ├── markdown.py    # HTML → Markdown conversion
│   ├── security.py    # Injection detection
│   ├── scoring.py     # Security scoring
│   ├── hashing.py     # Deterministic hashing
│   ├── tokens.py      # Token estimation
│   ├── stealth.py     # Basic bot evasion
│   ├── advanced_stealth.py  # Advanced anti-bot
│   ├── resource_blocker.py  # Resource optimization
│   ├── metadata_extractor.py # Metadata extraction
│   ├── link_analyzer.py     # Link analysis
│   ├── batch.py       # Batch processing
│   ├── cache.py       # Caching layer
│   ├── policy.py      # Policy engine
│   ├── plugin.py      # Plugin system
│   └── benchmark.py   # Benchmarking
├── models.py          # Data models (SafeDocument, etc.)
├── api.py             # Main API (ingest function)
├── api_server.py      # FastAPI server
├── cli.py             # Command-line interface
└── __init__.py        # Package exports
```

## Data Flow

```
URL Input
   ↓
Fetcher (fast mode) / Renderer (render mode)
   ↓
HTML Content
   ↓
Extractor (Readability + DOM cleaning)
   ↓
Cleaned HTML
   ↓
Normalizer (whitespace, unicode, URLs)
   ↓
Markdown Converter
   ↓
Sanitized Markdown
   ↓
Security Analyzer (injection detection)
   ↓
Hashing + Token Estimation
   ↓
SafeDocument (output)
```

## Module Descriptions

### Core Processing

**fetcher.py** - HTTP fetching with httpx
- Async HTTP GET requests
- Timeout handling
- User-agent configuration
- Basic error handling

**renderer.py** - Playwright browser automation
- Headless Chrome/Chromium
- JavaScript execution
- SPA support (wait for networkidle)
- Screenshot capture
- Stealth mode integration
- HTTP/2 fallback
- Resource blocking

**extractor.py** - Content extraction
- Mozilla Readability algorithm
- Remove nav, footer, aside, ads
- Detect and remove hidden elements (display:none, hidden, aria-hidden)
- Extract main content only

**normalizer.py** - Text normalization
- Normalize whitespace and line breaks
- Remove zero-width characters
- Unicode normalization (NFC)
- Clean tracking parameters from URLs
- Standardize heading hierarchy

**markdown.py** - HTML to Markdown conversion
- Uses markdownify library
- No inline HTML preserved
- Clean link formatting
- Code block preservation

### Security

**security.py** - Injection pattern detection
- Pattern matching for prompt injection attempts
- Detects meta-instructions like "ignore previous"
- Structural anomaly detection
- Imperative verb density analysis

**scoring.py** - Security scoring
- Weighted pattern hit scoring
- Hidden content penalties
- Imperative density calculation
- Returns 0.0 - 1.0 score

### Bot Evasion

**stealth.py** - Basic stealth mode
- 16 real browser user-agents (Chrome, Firefox, Safari, Edge)
- 21 Chromium arguments to hide automation
- Viewport randomization
- Context options (bypass_csp, ignore_https_errors)

**advanced_stealth.py** - Advanced anti-bot
- JavaScript injection to override navigator.webdriver
- chrome.runtime patching
- WebGL vendor spoofing
- Canvas fingerprint randomization
- Permissions API override
- 37 ultra-stealth browser arguments

**resource_blocker.py** - Performance optimization
- Blocks images, fonts, media, stylesheets
- Blocks ad/tracker domains
- Request interception via Playwright
- 40-60% speed improvement

### Enrichment

**metadata_extractor.py** - Metadata extraction
- Author, published/modified dates
- Language detection (langdetect)
- Schema.org parsing
- OpenGraph/Twitter Cards
- Content type classification

**link_analyzer.py** - Link analysis
- Extract all links
- Classify internal/external/anchor
- Domain analysis
- Link graph building

### Infrastructure

**batch.py** - Batch processing
- Concurrent URL processing
- Rich progress bars
- Error handling per URL
- Result aggregation

**cache.py** - Caching layer
- Memory cache (in-process)
- SQLite cache (persistent)
- Configurable TTL
- Hash-based keys

**policy.py** - Policy engine
- Configurable security rules
- Custom injection patterns
- Allowlist/blocklist
- Rule plugins

**plugin.py** - Plugin system
- Dynamic plugin loading
- Plugin discovery
- Hook system
- Custom extractors

**benchmark.py** - Performance benchmarking
- Token reduction metrics
- Speed comparisons
- Success rate tracking
- Statistical analysis

## Testing Strategy

### Unit Tests
- Test individual functions in isolation
- Mock external dependencies sparingly
- Focus on edge cases and error handling
- Fast execution (< 1 second each)

### Integration Tests
- Test full pipeline (URL → SafeDocument)
- Use real HTML samples (saved in tests/fixtures/)
- Test mode interactions (fast vs render)
- Test feature combinations

### Real-World Validation
- Alexa Top 50 as benchmark
- Test against actual live sites
- Measure success rate
- Track performance metrics

### Testing Philosophy
- **No mocks for core functionality** - Test with real HTML
- **Real Playwright for render tests** - Don't mock browser
- **Determinism is key** - Same input = same output
- **Coverage target**: >80% for core modules

## Adding New Features

### 1. Plan
- Discuss in GitHub issue first
- Define success criteria
- Consider backward compatibility

### 2. Implement
- Create new module in `core/` if needed
- Follow existing code style
- Add type hints
- Write docstrings

### 3. Integrate
- Update `api.py` if needed
- Add CLI flags to `cli.py` if applicable
- Update `SafeDocument` model if adding metadata

### 4. Test
- Write unit tests
- Write integration tests
- Test with real URLs
- Check edge cases

### 5. Document
- Update README.md
- Add to CHANGELOG.md
- Update examples/
- Add docstrings

### 6. Submit PR
- Follow CONTRIBUTING.md
- All tests passing
- Code formatted (black)
- Linting passing (ruff)

## Debugging Tips

### Enable Verbose Logging
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Test Specific URL
```bash
markdown-ingress ingest https://example.com --render --screenshot /tmp/debug.png
```

### Inspect SafeDocument
```python
from markdown_ingress import ingest
doc = ingest("https://example.com")
print(f"Tokens: {doc.token_estimate}")
print(f"Injection Score: {doc.injection_score}")
print(f"Flags: {doc.flags}")
print(f"Markdown length: {len(doc.markdown)}")
```

### Debug Playwright Issues
```python
from markdown_ingress.core.renderer import Renderer

renderer = Renderer(headless=False)  # Show browser
html = await renderer.render("https://example.com")
```

### Check Extraction Quality
```bash
# Save markdown to file
markdown-ingress ingest https://example.com -o output.md

# Review the markdown
cat output.md
```

## Performance Considerations

### Fast Mode vs Render Mode
- **Fast mode**: 1-2s, good for static pages
- **Render mode**: 3-8s, needed for SPAs
- **Auto mode**: Try fast first, fall back to render

### Batch Processing
- Use concurrent workers (default: 5)
- Monitor memory usage with large batches
- Consider caching for repeated URLs

### Resource Optimization
- Enable resource blocking for heavy sites
- Use stealth mode only when needed
- Set appropriate timeouts

### Token Optimization
- Average 92% token reduction vs raw HTML
- Readability algorithm removes boilerplate
- Hidden content filtering saves tokens

## Common Patterns

### Custom Security Rules
```python
from markdown_ingress.core.policy import Policy

policy = Policy()
policy.add_pattern(r"custom-bad-pattern", weight=0.5)
policy.strict = True

doc = ingest(url, policy=policy)
```

### Plugin Development
```python
from markdown_ingress.core.plugin import Plugin

class MyPlugin(Plugin):
    def process(self, html, url):
        # Custom processing
        return modified_html

plugin = MyPlugin()
plugin_loader.register(plugin)
```

### Batch with Caching
```python
from markdown_ingress.core.cache import SQLiteCache
from markdown_ingress.core.batch import BatchProcessor

cache = SQLiteCache("cache.db")
processor = BatchProcessor(cache=cache)
results = await processor.process_batch(urls)
```

## Release Process

1. Update version in `pyproject.toml` and `__init__.py`
2. Update CHANGELOG.md
3. Run all tests: `pytest`
4. Run linting: `ruff check . && black . --check`
5. Build package: `python -m build`
6. Test install: `pip install dist/markdown_ingress-*.whl`
7. Create git tag: `git tag v0.X.0`
8. Push tag: `git push origin v0.X.0`
9. GitHub Actions will publish to PyPI

## Resources

- **Python Style Guide**: PEP 8
- **Type Hints**: PEP 484
- **Playwright Docs**: https://playwright.dev/python/
- **Readability**: https://github.com/mozilla/readability
- **Markdownify**: https://github.com/matthewwithanm/python-markdownify

## Questions?

- GitHub Discussions for general questions
- GitHub Issues for bugs/features
- Email mriverolopez@gmail.com for private inquiries

---

Happy coding! 🚀
