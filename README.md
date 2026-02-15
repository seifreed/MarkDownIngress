<p align="center">
  <img src="https://img.shields.io/badge/MarkDownIngress-LLM%20Security-blue?style=for-the-badge" alt="MarkDownIngress">
</p>

<h1 align="center">MarkDownIngress</h1>

<p align="center">
  <strong>Deterministic, Injection-Resistant Web → Markdown Engine for LLM Pipelines</strong>
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square&logo=python&logoColor=white" alt="Python Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/tests-51%20passing-brightgreen?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/version-0.3.0-orange?style=flat-square" alt="Version">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/tokens-78%25%20reduction-success?style=flat-square" alt="Token Reduction">
  <img src="https://img.shields.io/badge/security-injection%20detection-red?style=flat-square" alt="Security">
  <img src="https://img.shields.io/badge/mode-deterministic-purple?style=flat-square" alt="Deterministic">
</p>

---

## Overview

**MarkDownIngress** is a security-first web content ingestion engine designed specifically for LLM pipelines. It extracts, sanitizes, and analyzes web content while detecting prompt injection attempts, producing deterministic Markdown output optimized for token efficiency.

### What It Is **NOT**

| ❌ | Description |
|---|-------------|
| Web Crawler | Not designed for recursive site crawling |
| RAG Framework | Not a complete RAG solution |
| HTML Converter | Not a generic HTML→Markdown tool |

### What It **IS**

| ✅ | Description |
|---|-------------|
| **Ingestion Security Engine** | Detects and flags prompt injection attempts |
| **Token Optimizer** | Reduces token count by 70-80% on average |
| **Deterministic Processor** | Same input = same output, always |
| **LLM Pipeline Component** | Drop-in solution for safe content ingestion |

### Processing Pipeline

```
Untrusted Web URL
        ↓
🌐 HTTP Fetch (Fast Mode)
        ↓
📄 Content Extraction (Readability)
        ↓
🧹 Sanitization (Remove Scripts/Tracking)
        ↓
🔒 Security Analysis (Injection Detection)
        ↓
📝 Markdown Conversion
        ↓
✅ Safe, Deterministic Output
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Fast Mode** | HTTP-only fetching (no JS execution) |
| **Render Mode** | Playwright-based rendering for SPAs |
| **Batch Processing** | ✨ **NEW v0.3** Process multiple URLs concurrently |
| **Caching** | ✨ **NEW v0.3** Memory & SQLite caching with TTL |
| **Policy Engine** | ✨ **NEW v0.3** Configurable security policies |
| **Security Analysis** | Pattern-based prompt injection detection |
| **Token Estimation** | Accurate token counts via tiktoken |
| **Content Hashing** | SHA256 for deduplication/versioning |
| **Hidden Content Detection** | Finds `display:none`, `hidden`, `aria-hidden` |
| **URL Sanitization** | Removes tracking params (utm_*, fbclid, etc.) |
| **Library + CLI** | Use as Python API or command-line tool |
| **Deterministic** | Reproducible output for caching |

### Supported Features

```
Extraction         Mozilla Readability algorithm
Cleaning           nav, footer, aside, script, style removal
Normalization      Unicode NFC, zero-width char removal
Security           10+ injection pattern detectors
Risk Scoring       0.0 (safe) → 1.0 (critical)
Token Models       GPT-4, Claude, GPT-3.5-turbo, etc.
Output Formats     Markdown, JSON, SafeDocument
Modes              Fast (HTTP), Render (Playwright) ✨ NEW
JavaScript         Fully rendered SPAs with Playwright ✨ NEW
```

---

## Installation

### From Source

```bash
git clone https://github.com/yourusername/MarkDownIngress.git
cd MarkDownIngress
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

### With Render Mode (Playwright)

```bash
# Install with render mode support
pip install -e ".[render]"

# Install browser for Playwright
playwright install chromium
```

### Quick Install (Development)

```bash
pip install -e .
```

---

## Quick Start

### Command Line Interface

```bash
# Basic ingestion (fast mode - no JS)
markdown-ingress https://example.com

# Render mode for JavaScript-heavy sites (NEW in v0.2)
markdown-ingress https://spa-app.com --render

# Save markdown output
markdown-ingress https://example.com --save output.md

# JSON output with metadata
markdown-ingress https://example.com --json --save output.json

# Specify token model
markdown-ingress https://example.com --model claude

# Permissive mode (lower security threshold)
markdown-ingress https://example.com --permissive
```

### Example Output

```
============================================================
MarkDownIngress v0.2.0 - Ingestion Report
============================================================

📄 Title: Example Domain
🔗 URL: http://example.com

✔ Tokens: 33
  ↳ Saved: 119 tokens (78.29% reduction)

🔒 Injection Score: 0.000 (SAFE)

🔑 Hash: sha256:d6ac852cf2392c04d2cf3e3e4156f786cfbc4f46308ebe756ebd72cf9ffef4ef
⏱️  Fetch time: 116ms
```

---

## Usage

### Python Library

#### Basic Usage

```python
from markdown_ingress import ingest

# Ingest URL and get sanitized markdown
doc = ingest("https://example.com", mode="fast", strict=True)

print(doc.markdown)              # Clean markdown content
print(doc.token_estimate)        # Token count
print(doc.injection_score)       # 0.0-1.0 risk score
print(doc.content_hash)          # SHA256 hash
print(doc.flags)                 # Security warnings
```

#### Advanced Usage

```python
from markdown_ingress import ingest
from markdown_ingress.core.scoring import Scorer

# Fast mode (HTTP only, no JavaScript)
doc_fast = ingest(
    url="https://blog.example.com/article",
    mode="fast",
    strict=True,
    model="gpt-4",
    timeout=30.0
)

# Render mode (with JavaScript execution) - NEW in v0.2
doc_render = ingest(
    url="https://react-app.com",
    mode="render",  # Uses Playwright
    strict=True,
    model="gpt-4",
    timeout=60.0  # Render mode needs more time
)

# Analyze security
scorer = Scorer()
risk_level = scorer.get_risk_level(doc_fast.injection_score)
recommendation = scorer.get_recommendation(doc_fast.injection_score)

print(f"Risk Level: {risk_level}")
print(f"Recommendation: {recommendation}")

# Access metadata
print(f"Title: {doc_fast.metadata['title']}")
print(f"Mode: {doc_fast.metadata['mode']}")  # 'fast' or 'render'
print(f"Fetch time: {doc_fast.metadata['fetch_time_ms']}ms")
print(f"Token savings: {doc_fast.metadata['token_savings']}")
```

#### Batch Processing

```python
from markdown_ingress import ingest

urls = [
    "https://example.com/article1",
    "https://example.com/article2",
    "https://example.com/article3",
]

safe_docs = []
for url in urls:
    doc = ingest(url)
    if doc.injection_score < 0.3:  # Safe threshold
        safe_docs.append(doc)
        print(f"✓ {url}: {doc.token_estimate} tokens")
    else:
        print(f"⚠ {url}: High risk ({doc.injection_score})")

print(f"\nSafe documents: {len(safe_docs)}/{len(urls)}")
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `url` | Target URL to ingest (positional) |
| `--render` | ✨ **NEW** Use render mode (Playwright for SPAs) |
| `--strict` | Enable strict security mode (default) |
| `--permissive` | Disable strict mode |
| `--model MODEL` | LLM model for token estimation (default: gpt-4) |
| `--timeout TIMEOUT` | Request timeout in seconds (default: 30) |
| `--json` | Output as JSON |
| `--save FILE` | Save output to file |
| `--version` | Show version |

---

## API Reference

### Main Function

```python
ingest(
    url: str,
    mode: Literal["fast", "render"] = "fast",
    strict: bool = True,
    model: str = "gpt-4",
    timeout: float = 30.0
) -> SafeDocument
```

**Parameters:**

- `url` — Target URL to ingest
- `mode` — Fetching mode: `"fast"` (HTTP only) or `"render"` (Playwright with JS) ✨ **NEW**
- `strict` — Enable strict security mode
- `model` — LLM model for token estimation (`gpt-4`, `claude`, `gpt-3.5-turbo`)
- `timeout` — Request timeout in seconds

**Returns:** `SafeDocument` object

### SafeDocument Object

```python
@dataclass
class SafeDocument:
    markdown: str              # Cleaned markdown content
    metadata: dict             # URL, title, timing, token savings
    token_estimate: int        # Token count for specified model
    content_hash: str          # SHA256 hash (format: "sha256:...")
    injection_score: float     # 0.0 (safe) to 1.0 (critical)
    flags: list[str]           # Security warning flags
    removed_elements: dict     # Removed tags and hidden elements
```

---

## Examples

### LangChain Integration

```python
from langchain.document_loaders import BaseLoader
from langchain.schema import Document
from markdown_ingress import ingest

class MarkDownIngressLoader(BaseLoader):
    def __init__(self, url: str, strict: bool = True):
        self.url = url
        self.strict = strict
    
    def load(self) -> list[Document]:
        doc = ingest(self.url, strict=self.strict)
        
        return [Document(
            page_content=doc.markdown,
            metadata={
                "source": doc.metadata['url'],
                "title": doc.metadata['title'],
                "injection_score": doc.injection_score,
                "hash": doc.content_hash
            }
        )]

# Usage
loader = MarkDownIngressLoader("https://example.com/article")
docs = loader.load()
```

### FastAPI Endpoint

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from markdown_ingress import ingest

app = FastAPI()

class IngestRequest(BaseModel):
    url: HttpUrl
    strict: bool = True
    model: str = "gpt-4"

@app.post("/ingest")
async def ingest_url(request: IngestRequest):
    try:
        doc = ingest(str(request.url), strict=request.strict, model=request.model)
        
        return {
            "markdown": doc.markdown,
            "tokens": doc.token_estimate,
            "injection_score": doc.injection_score,
            "hash": doc.content_hash,
            "risk_level": doc.metadata['risk_level']
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### Deduplication Using Hashes

```python
from markdown_ingress import ingest

seen_hashes = set()
unique_docs = []

for url in urls:
    doc = ingest(url)
    
    if doc.content_hash not in seen_hashes:
        seen_hashes.add(doc.content_hash)
        unique_docs.append(doc)
    else:
        print(f"Duplicate content: {url}")

print(f"Unique: {len(unique_docs)} / Total: {len(urls)}")
```

---

## Security Features

### Injection Detection Patterns

MarkDownIngress detects common prompt injection patterns:

| Pattern | Weight | Example |
|---------|--------|---------|
| Instruction Override | 0.8 | "ignore previous instructions" |
| System Prompt Access | 0.6 | "reveal system prompt" |
| Mode Switching | 0.7 | "enable developer mode" |
| Secret Extraction | 0.9 | "reveal secret keys" |
| Model Manipulation | 0.5 | "you are ChatGPT" |
| Policy Override | 0.8 | "override policy settings" |

### Risk Levels

| Score | Level | Action |
|-------|-------|--------|
| 0.0 - 0.2 | **SAFE** | ✅ Content appears safe |
| 0.2 - 0.4 | **LOW** | ⚠️ Review recommended |
| 0.4 - 0.6 | **MEDIUM** | ⚠️ Manual review required |
| 0.6 - 0.8 | **HIGH** | 🚫 Use with caution |
| 0.8 - 1.0 | **CRITICAL** | 🚫 Blocking recommended |

---

## Architecture

```
markdown_ingress/
    core/
        fetcher.py       # HTTP client (httpx)
        extractor.py     # Content extraction (readability-lxml + selectolax)
        normalizer.py    # Unicode/whitespace normalization
        markdown.py      # HTML → Markdown conversion (markdownify)
        security.py      # Injection pattern detection
        scoring.py       # Risk level calculation
        hashing.py       # Deterministic SHA256 hashing
        tokens.py        # Token estimation (tiktoken)
    models.py            # Data models (SafeDocument, etc.)
    api.py               # Main ingest() function
    cli.py               # Command-line interface
```

---

## Roadmap

| Version | Status | Features |
|---------|--------|----------|
| **v0.1** | ✅ Released | Fast mode, injection detection, CLI, token estimation |
| **v0.2** | ✅ Released | Playwright render mode, SPA support |
| **v0.3** | ✅ **Current** | ✨ Batch processing, caching, policy engine, 51 tests |
| **v0.4** | 📋 Planned | Enhanced reports, benchmarking, plugin discovery |

---

## Requirements

- Python 3.11+
- Core dependencies:
  - `httpx` — Async HTTP client
  - `selectolax` — Fast HTML parser
  - `readability-lxml` — Content extraction
  - `markdownify` — HTML → Markdown conversion
  - `tiktoken` — Token counting
- Optional (for render mode):
  - `playwright` — Headless browser automation ✨ **NEW**

See [pyproject.toml](pyproject.toml) for complete dependency list.

---

## Development

### Setup

```bash
git clone https://github.com/yourusername/MarkDownIngress.git
cd MarkDownIngress
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest tests/ -v                    # Run all tests
pytest tests/ --cov=markdown_ingress  # With coverage
```

### Project Stats

- **35+ files** created
- **2,600+ lines** of Python code
- **51 tests** (100% passing)
- **11 core modules** + API + CLI + Renderer + Batch + Cache + Policy

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Add tests for new functionality
4. Ensure all tests pass (`pytest tests/ -v`)
5. Submit a pull request

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## FAQ

**Q: Why not just use Pandoc or html2text?**  
A: Those are converters. MarkDownIngress is an **ingestion security engine** with injection detection, deterministic hashing, and LLM-optimized output.

**Q: Does it work with JavaScript-heavy sites?**  
A: ✨ **Yes!** v0.2 includes Playwright render mode for full SPA support. Use `mode="render"` or `--render` flag.

**Q: How accurate is the injection detection?**  
A: Pattern-based heuristics catch common attacks. Not ML-based, but highly effective for known patterns. Customize for your use case.

**Q: Can I use this in production?**  
A: v0.2 is beta. Fast mode is stable. Render mode is production-ready but slower. Review security scores for critical applications.

**Q: How is this different from Trafilatura/Newspaper3k?**  
A: Those focus on article extraction. We add **security analysis**, **deterministic hashing**, and **LLM-specific token optimization**.

---

<p align="center">
  <sub>Built for the LLM era. Secure by default. Deterministic by design.</sub>
</p>
