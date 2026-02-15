# Changelog

All notable changes to MarkDownIngress will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.4.2] - 2025-02-15

### Added
- **Stealth Mode**: Advanced bot detection bypass system
  - `markdown_ingress/core/stealth.py` module with 16 real browser user-agents
  - Chrome 120-121, Firefox 121-122, Safari 17, Edge 120-121 user-agents
  - 21 Chromium browser arguments to hide automation signatures
  - Viewport randomization (6 common sizes: 1920x1080, 1366x768, etc.)
  - Context options for Playwright with bypass_csp and ignore_https_errors
  
- **Retry Logic**: Intelligent retry with exponential backoff
  - `retry_ingest()` function in api.py
  - Automatic timeout escalation: 60s → 90s → 120s → 150s
  - Stealth mode automatically enabled on 2nd+ attempts
  - Comprehensive retry metadata tracking (attempts, timeout, stealth_enabled)
  
- **HTTP/2 Protocol Fallback**: Automatic error handling
  - Detects `ERR_HTTP2_PROTOCOL_ERROR` in renderer.py
  - Automatic retry with `--disable-http2` browser flag
  - HTTP/1.1 fallback for protocol-incompatible sites
  - Metadata flag: `http2_fallback` to track fallback usage

### Changed
- Renderer class now supports `stealth` and `disable_http2` parameters
- Browser context includes bypass_csp and ignore_https_errors by default
- Improved error messages and retry logging

### Performance
- **Success rate improved**: 82% on Alexa Top 50 (up from 80%)
- Successfully parses 4 previously failed sites:
  - medium.com (31 tokens)
  - indeed.com (17 tokens)
  - paypal.com (33 tokens)
  - walmart.com (46 tokens)
- Auto mode statistics: 58% fast mode, 42% render mode

### Tested
- Alexa Top 50 validation: 41/50 success (82%)
- Stealth mode tested with bot-protected sites
- HTTP/2 fallback tested with adobe.com, costco.com
- All 117 tests passing (added 9 new tests)

### Documentation
- Updated README with stealth mode and retry logic examples
- Added user-agent documentation
- Updated API examples with new features

---

## [0.4.1] - 2025-02-15

### Added
- **Automatic Mode Detection**: New `auto` mode intelligently switches between fast and render modes
  - Tries fast mode first (HTTP-only, fast & cheap)
  - Auto-upgrades to render mode if content is minimal (< 50 tokens)
  - Returns whichever mode provides better content
  - Configurable threshold via `auto_render_threshold` parameter
- Metadata tracking for auto mode decisions:
  - `auto_mode_used`: Which mode was ultimately used ("fast" or "render")
  - `fast_mode_tokens`: Token count from fast mode attempt (for comparison)

### Changed
- **Breaking**: Default mode changed from `"fast"` to `"auto"` in both CLI and API
- CLI now supports `--auto`, `--fast`, and `--render` flags for explicit mode selection
- Batch processing now supports auto mode with concurrent processing
- Batch JSON output now includes full metadata for analysis

### Tested
- Alexa Top 50 validation: 80% success rate (40/50 sites)
- Auto mode correctly identified SPAs in 47.5% of cases
- Sites like Wikipedia, Twitter auto-upgraded from 1 token → 355-666 tokens
- See ALEXA_TOP50_AUTO_MODE_REPORT.md for detailed analysis

---

## [0.4.0] - 2026-02-15

### Added
- **Structural Hashing**: Advanced hash_structural() method that captures document structure independently of content changes
  - Detects heading hierarchy, list structure, code blocks, and links
  - Case-insensitive and punctuation-normalized
  - Same structure produces same hash even with different content
- **Security Reports**: Comprehensive SecurityReport dataclass with JSON export
  - generate_security_report() API function
  - Detailed metrics: pattern matches, hidden content, imperative density
  - Token reduction statistics and size metrics
  - Save/load to JSON files with full metadata
- **Configuration File Support**: YAML/JSON config files with environment variable overrides
  - load_config() function with automatic discovery
  - Default locations: .markdowningress.yaml, ~/.config/markdowningress/
  - Environment variables with MDI_ prefix (e.g., MDI_MODE, MDI_STRICT)
  - Config.to_yaml() and Config.to_json() for export
- **CLI Batch Command**: Process multiple URLs concurrently
  - `markdown-ingress batch urls.txt --output results/`
  - Progress bar with rich library
  - JSON summary output option
  - Configurable concurrency limit
  - Automatic comment/empty line filtering
- **Plugin System**: Extensible pattern injection via plugins
  - Plugin base class with get_patterns() method
  - PluginLoader with directory discovery
  - Load custom security patterns dynamically
  - Plugin lifecycle hooks (on_load, on_unload)
- **Benchmarking Suite**: Performance and quality metrics
  - Benchmark.run_single() and run_batch()
  - Timing statistics (avg, min, max, stddev)
  - Token and size reduction metrics
  - Text report generation

### Changed
- Updated __version__ to "0.4.0"
- CLI now uses subcommands (ingest, batch) for better organization
- api.py now generates structural_hash alongside content_hash
- models.py imports timezone for Python 3.14 compatibility

### Fixed
- datetime.utcnow() deprecation warning (now uses datetime.now(timezone.utc))

### Tests
- Added 57 new tests (108 total, all passing)
- Zero mocks - all tests use real implementations
- Test coverage: structural hash (12), security reports (9), config (18), CLI batch (8), plugins (6), benchmarking (4)

---

## [0.3.0] - 2026-02-14

### Added
- **Batch Processing**: Concurrent URL processing with BatchProcessor
  - process_batch_async() for async batch ingestion
  - Semaphore-based concurrency control
  - Progress callbacks
  - BatchResult with success/failure tracking
- **Caching Layer**: Memory and SQLite caching
  - Cache interface with get/set/clear/has methods
  - MemoryCache for session-based caching
  - SQLiteCache with TTL support and persistence
  - Automatic cache key generation from URL+mode+strict
- **Policy Engine**: Configurable security policies
  - 4 predefined policies: permissive, moderate, strict, paranoid
  - Custom pattern support
  - Policy.from_dict() and to_dict() serialization
  - PolicyEngine.from_name() for easy selection
- Advanced usage documentation (ADVANCED.md)
- 23 new tests for batch, cache, and policy features

### Changed
- Version bumped to 0.3.0
- Extended __init__.py exports with batch, cache, policy classes

---

## [0.2.0] - 2026-02-13

### Added
- **Render Mode**: Full SPA support with Playwright
  - renderer.py module with async browser automation
  - render_async() and render() methods
  - Configurable wait strategies (networkidle, load, domcontentloaded)
  - Headless browser operation
  - Custom user agent support
- ingest() now supports mode="render" parameter
- 7 new renderer tests
- Optional playwright dependency via `pip install '.[render]'`

### Changed
- Version bumped to 0.2.0
- Fast mode remains default (~800ms)
- Render mode available when Playwright installed (~1-3s)

### Fixed
- Graceful degradation when Playwright not available

---

## [0.1.0] - 2026-02-12

### Added
- **Core Functionality**:
  - ingest() API for web → Markdown conversion
  - SafeDocument output model with security analysis
  - Deterministic content hashing (SHA256)
  - Token estimation with tiktoken
- **Security Features**:
  - Injection pattern detection (10+ patterns)
  - Hidden content removal
  - Imperative density analysis
  - Risk scoring (0.0-1.0) with levels (safe/low/medium/high/critical)
  - Strict vs permissive modes
- **Core Modules**:
  - fetcher.py: HTTP client with httpx
  - extractor.py: Content extraction with readability-lxml
  - normalizer.py: Unicode and whitespace normalization
  - markdown.py: HTML to Markdown conversion
  - hashing.py: Deterministic fingerprinting
  - tokens.py: Token counting
  - security.py: Pattern-based injection detection
  - scoring.py: Risk level calculation
- **CLI**: markdown-ingress command with argparse
  - Pretty terminal output with colors
  - JSON export option
  - Save to file
  - Configurable model, timeout, strict mode
- **Documentation**:
  - Professional README in IOCParser style
  - Usage examples (EXAMPLES.md)
  - MIT License
  - Comprehensive .gitignore
- **Testing**:
  - 21 core tests covering all modules
  - Integration tests
  - Determinism tests
  - Zero mocks

### Technical Details
- Python 3.11+ required
- Dependencies: httpx, selectolax, readability-lxml, markdownify, tiktoken
- Fallback extraction for landing pages when readability fails
- SSL certificate handling for macOS

---

## [0.0.1] - Initial Concept

### Added
- Project specification and design document
- Roadmap (v0.1-v0.3)
- Core architecture planning

