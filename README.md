# MarkDownIngress

**Deterministic, Injection-Resistant Web → Markdown Engine for LLM Pipelines**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## What is MarkDownIngress?

MarkDownIngress is **not**:
- ❌ A web crawler
- ❌ A RAG framework  
- ❌ An HTML converter

MarkDownIngress **is**:
- ✅ A **deterministic, injection-aware web ingestion engine** for LLM pipelines
- ✅ A **security-first** content processor that detects prompt injection attempts
- ✅ A **token-efficient** converter that strips noise and produces clean Markdown

```
Untrusted Web URL
        ↓
Deterministic Extraction
        ↓
Sanitized Markdown
        ↓
Injection Analysis
        ↓
Structured Safe Output for LLM
```

---

## Why MarkDownIngress?

When ingesting web content for LLM processing, you need:

1. **Token Efficiency** — Strip HTML bloat, navigation, ads, tracking scripts
2. **Security** — Detect hidden prompt injection attempts in web content
3. **Determinism** — Same input = same output, every time (for caching/dedup)
4. **Auditability** — Know what was removed and why
5. **Reproducibility** — Content hashing for version control

MarkDownIngress does all of this in a single function call.

---

## Quick Start

### Installation

```bash
pip install markdown-ingress
```

### Basic Usage

```python
from markdown_ingress import ingest

# Ingest a URL
doc = ingest("https://example.com", mode="fast", strict=True)

# Access the sanitized markdown
print(doc.markdown)

# Check security analysis
print(f"Injection score: {doc.injection_score}")  # 0.0 - 1.0
print(f"Flags: {doc.flags}")

# Get token count
print(f"Tokens: {doc.token_estimate}")

# Deterministic content hash
print(f"Hash: {doc.content_hash}")
```

### CLI Usage

```bash
# Basic ingestion
markdown-ingress https://example.com

# Save to file
markdown-ingress https://example.com --save output.md

# JSON output
markdown-ingress https://example.com --json --save output.json

# Specify model for token counting
markdown-ingress https://example.com --model gpt-4

# Permissive mode (disable strict security)
markdown-ingress https://example.com --permissive
```

**Example Output:**

```
============================================================
MarkDownIngress v0.1.0 - Ingestion Report
============================================================

📄 Title: Example Domain
🔗 URL: https://example.com

✔ Tokens: 1,432
  ↳ Saved: 3,241 tokens (69.3% reduction)

🔒 Injection Score: 0.210 (LOW)
⚠️  Flags: hidden_content

🗑️  Removed tags: script:5, style:3, nav:2
🗑️  Removed hidden elements: 3

🔑 Hash: sha256:abc123...
⏱️  Fetch time: 342ms
```

---

## Features

### 🎯 Core Capabilities

- **Fast Mode** — HTTP-only fetching (no JavaScript rendering)
- **Content Extraction** — Uses Mozilla Readability algorithm
- **Aggressive Cleaning** — Removes `nav`, `footer`, `aside`, `script`, `style`, hidden elements
- **Unicode Normalization** — NFC normalization, zero-width character removal
- **URL Sanitization** — Strips tracking parameters (`utm_*`, `fbclid`, etc.)
- **Markdown Conversion** — Clean, consistent output format
- **Token Estimation** — Uses `tiktoken` for accurate LLM token counts
- **Content Hashing** — SHA256 for deterministic fingerprinting

### 🔒 Security Features

- **Prompt Injection Detection** — Pattern-based heuristics for common attacks
- **Hidden Content Analysis** — Detects `display:none`, `hidden`, `aria-hidden` elements
- **Imperative Density Scoring** — Flags high concentration of command verbs
- **Risk Scoring** — 0.0 (safe) to 1.0 (critical) injection risk score
- **Strict Mode** — Configurable sensitivity levels

**Detected Patterns:**
- "ignore previous instructions"
- "system prompt" references
- "developer mode" activation attempts
- Secret extraction attempts
- Model identity manipulation
- And more...

---

## API Reference

### `ingest(url, mode="fast", strict=True, model="gpt-4", timeout=30.0)`

**Parameters:**

- `url` (str) — Target URL to ingest
- `mode` (str) — `"fast"` (HTTP only) or `"render"` (with JS, v0.2+)
- `strict` (bool) — Enable strict security mode (default: `True`)
- `model` (str) — LLM model for token estimation (default: `"gpt-4"`)
- `timeout` (float) — Request timeout in seconds (default: `30.0`)

**Returns:** `SafeDocument` with:

```python
@dataclass
class SafeDocument:
    markdown: str              # Cleaned markdown content
    metadata: dict             # URL, title, timing, etc.
    token_estimate: int        # Token count for specified model
    content_hash: str          # SHA256 hash (format: "sha256:...")
    injection_score: float     # 0.0 - 1.0 risk score
    flags: list[str]           # Security warning flags
    removed_elements: dict     # What was stripped during cleaning
```

---

## Architecture

```
markdown_ingress/
    core/
        fetcher.py       # HTTP client (httpx)
        extractor.py     # Content extraction (readability + selectolax)
        normalizer.py    # Unicode + whitespace normalization
        markdown.py      # HTML → Markdown conversion
        security.py      # Injection pattern detection
        scoring.py       # Risk level calculation
        hashing.py       # Deterministic content hashing
        tokens.py        # Token estimation (tiktoken)
    models.py            # Data models
    api.py               # Main ingest() function
    cli.py               # Command-line interface
```

---

## Roadmap

### v0.1 ✅ (Current)
- Fast mode (HTTP-only)
- Injection heuristics
- CLI
- Token estimation
- Deterministic hashing

### v0.2 (Planned)
- Render mode (Playwright for SPA)
- Structural hashing
- Enhanced security report JSON
- Custom pattern plugins

### v0.3 (Future)
- Configurable policy engine
- Rule plugin system
- Batch ingestion
- Caching layer

---

## Design Principles

1. **Deterministic-first** — Same input always produces same output
2. **Security-by-default** — Aggressive detection, configurable strictness
3. **No telemetry** — Zero external network calls beyond target URL
4. **Modular** — Composable components, easy to extend
5. **Fast-path** — HTTP-only mode for speed (JS rendering optional)
6. **Auditable** — Full transparency on what was removed/modified

---

## Use Cases

- **LLM RAG Pipelines** — Clean web content before embedding
- **Content Moderation** — Detect injection attempts in user-submitted URLs
- **Web Scraping** — Deterministic extraction with deduplication
- **Security Research** — Analyze prompt injection patterns in the wild
- **Documentation Processing** — Convert web docs to clean Markdown

---

## Requirements

- Python 3.11+
- Dependencies:
  - `httpx` — Async HTTP client
  - `selectolax` — Fast HTML parser
  - `readability-lxml` — Content extraction
  - `markdownify` — HTML → Markdown
  - `tiktoken` — Token counting

---

## Development

```bash
# Clone repository
git clone https://github.com/yourusername/MarkDownIngress.git
cd MarkDownIngress

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=markdown_ingress --cov-report=html
```

---

## FAQ

**Q: Why not just use Pandoc or html2text?**  
A: Those are converters. MarkDownIngress is an **ingestion security engine** with injection detection, deterministic hashing, and LLM-optimized output.

**Q: Does it work with JavaScript-heavy sites?**  
A: v0.1 supports fast mode (static HTML). Render mode (Playwright) is coming in v0.2.

**Q: How accurate is the injection detection?**  
A: Pattern-based heuristics catch common attacks. It's not ML-based, so customize patterns for your use case.

**Q: Can I use this in production?**  
A: v0.1 is alpha. Use with caution and review security scores manually.

**Q: How is this different from Trafilatura/Newspaper3k?**  
A: Those focus on article extraction. We add security analysis, deterministic hashing, and LLM-specific optimizations.

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure `pytest` passes
5. Submit a pull request

---

## Citation

If you use MarkDownIngress in research, please cite:

```bibtex
@software{markdowningress2024,
  title = {MarkDownIngress: Deterministic, Injection-Resistant Web to Markdown Engine},
  author = {MarkDownIngress Contributors},
  year = {2024},
  url = {https://github.com/yourusername/MarkDownIngress}
}
```

---

**Built for the LLM era. Secure by default. Deterministic by design.**
