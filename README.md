<p align="center">
  <img src="https://img.shields.io/badge/MarkDownIngress-LLM%20Security-blue?style=for-the-badge" alt="MarkDownIngress">
</p>

<h1 align="center">MarkDownIngress</h1>

<p align="center">
  <strong>Deterministic, Injection-Resistant Web → Markdown Engine for LLM Pipelines</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/markdown-ingress/"><img src="https://badge.fury.io/py/markdown-ingress.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://github.com/seifreed/MarkDownIngress/actions"><img src="https://github.com/seifreed/MarkDownIngress/workflows/CI/badge.svg" alt="Tests"></a>
  <img src="https://img.shields.io/badge/coverage-63%25-orange.svg" alt="Coverage">
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
| **Deterministic Processor** | Stable output in `fast` mode; render/stealth trades strict determinism for better coverage |
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
| **Batch Processing** | Process multiple URLs concurrently (v0.3) |
| **Caching** | Memory & SQLite caching with TTL (v0.3) |
| **Policy Engine** | Configurable security policies (v0.3) |
| **Structural Hashing** | ✨ **NEW v0.4** Structure-aware fingerprinting |
| **Security Reports** | ✨ **NEW v0.4** Comprehensive JSON reports |
| **Config Files** | ✨ **NEW v0.4** YAML/JSON configuration support |
| **CLI Batch Command** | ✨ **NEW v0.4** `markdown-ingress batch urls.txt` |
| **Plugin System** | ✨ **NEW v0.4** Custom injection pattern plugins |
| **Benchmarking** | ✨ **NEW v0.4** Performance metrics suite |
| **Output Profiles** | Presets for `llm_safe`, `rag_chunkable`, `for_search`, `for_archive` |
| **Domain Policies** | Host-specific overrides for mode, policy, selectors, allowed/blocked tags |
| **Structured Blocks** | Block-level extraction for headings, tables, code, quotes and lists |
| **Native Chunking** | Stable chunks with structural hashes, offsets and token estimates |
| **Observability** | Stage timings, policy actions, queue stats and render fallback tracing |
| **Persistent Batch Jobs** | Versioned API jobs with polling, TTL cleanup and optional webhooks |
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
git clone https://github.com/seifreed/MarkDownIngress.git
cd MarkDownIngress
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

### With Render Mode (Playwright)

```bash
# Install base package plus render support
pip install -e ".[render]"

# Install browser for Playwright
playwright install chromium
```

### With Advanced Security (Nova)

```bash
# Install Nova-based advanced detection
pip install -e ".[security]"
```

### Quick Install (Core Only)

```bash
pip install -e .
```

---

## Quick Start

### Command Line Interface

```bash
# Single URL ingestion
markdown-ingress ingest https://example.com

# Single URL ingestion using a config file
markdown-ingress ingest https://example.com --config .markdowningress.yaml

# Batch processing (NEW in v0.4)
markdown-ingress batch urls.txt --output results/

# Render mode for JavaScript-heavy sites (v0.2)
markdown-ingress ingest https://spa-app.com --render

# Save markdown output
markdown-ingress ingest https://example.com --save output.md

# JSON output with metadata
markdown-ingress ingest https://example.com --json --save output.json

# Batch with JSON summary (v0.4)
markdown-ingress batch urls.txt --json --output summary.json

# Specify token model
markdown-ingress ingest https://example.com --model claude

# Permissive mode (lower security threshold)
markdown-ingress ingest https://example.com --permissive
```

### Example Output

```
============================================================
MarkDownIngress v0.8.0 - Ingestion Report
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
from markdown_ingress import DomainPolicy, ingest
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

# Profile-driven structured output for RAG
doc_rag = ingest(
    url="https://docs.example.com/guide",
    mode="fast",
    output_profile="rag_chunkable",
    extract_blocks=True,
    chunking_strategy="heading",
)

print(doc_rag.structured_blocks[0]["block_type"])
print(doc_rag.chunks[0]["chunk_id"])

# Domain-specific hardening
doc_policy = ingest(
    url="https://forum.example.com/thread",
    mode="auto",
    domain_policies=[
        DomainPolicy(
            domain="forum.example.com",
            output_profile="llm_safe",
            policy_name="strict",
            blocked_selectors=[".reply-box", ".promo"],
            blocked_tags=["form"],
        )
    ],
)

print(doc_policy.metadata["domain_policy"])
```

#### Batch Processing

```python
from markdown_ingress import ingest_many

urls = [
    "https://example.com/article1",
    "https://example.com/article2",
    "https://example.com/article3",
]

result = ingest_many(
    urls,
    mode="auto",
    timeout=20.0,
    max_concurrent=4,
)

safe_docs = [
    doc
    for doc in result.documents
    if doc is not None and doc.injection_score < 0.3
]
errors_by_index = {error.index: error.error for error in result.error_items}

for index, (url, doc) in enumerate(zip(urls, result.documents, strict=False)):
    if doc is None:
        print(f"✗ {url}: {errors_by_index.get(index, 'unknown error')}")
    else:
        print(f"✓ {url}: {doc.token_estimate} tokens")

print(f"\nSafe documents: {len(safe_docs)}/{result.total}")
```

When multiple concurrent requests target the same URL with the same effective config,
MarkDownIngress deduplicates the in-flight work. Result metadata exposes:
`cache_hit`, `inflight_deduplicated`, and `inflight_shared_count`.
For process-level observability, the library also exposes
`get_ingest_stats()` and `reset_ingest_stats()`, including
`mode_counts`, `mode_timings_ms`, and `mode_results` for `fast`, `render`, and `auto`.
See [examples/library_batch_async.py](examples/library_batch_async.py)
for a complete batch example that prints these metrics.

#### Async Integration

```python
import asyncio

from markdown_ingress import ingest_async, ingest_many_async


async def main():
    single = await ingest_async("https://example.com", mode="auto")
    print(single.metadata["title"])

    batch = await ingest_many_async(
        ["https://example.com", "https://example.org"],
        mode="fast",
        max_concurrent=5,
    )
    print(batch.successful, batch.failed)


asyncio.run(main())
```

### v0.4 Features

#### Configuration Files

```python
from markdown_ingress import ingest
from markdown_ingress.core.config import load_config

# Auto-discover config from default locations
config = load_config()

# Or specify path explicitly  
config = load_config("my_config.yaml")

# Use config values with the public API
doc = ingest("https://example.com", config=config.to_ingest_config())

print(config.mode)                   # 'auto', 'fast' or 'render'
print(config.cache_enabled)          # True/False
print(config.batch_max_concurrent)   # 5
```

**YAML example (.markdowningress.yaml):**

```yaml
mode: fast
timeout: 45.0
strict: true
cache_enabled: true
cache_type: sqlite
batch_max_concurrent: 10
policy: normal
```

**Environment variable override:**

```bash
export MDI_MODE=render
export MDI_CACHE_ENABLED=true
export MDI_BATCH_MAX_CONCURRENT=20
```

#### Security Reports

```python
from markdown_ingress import generate_security_report

# Generate comprehensive security report
report = generate_security_report("https://suspicious-site.com")

# Export to JSON
report.save("security_report.json")

# Access detailed metrics
print(f"Injection score: {report.injection_score}")
print(f"Risk level: {report.risk_level}")
print(f"Token reduction: {report.token_reduction_percent}%")
print(f"Pattern matches: {report.pattern_matches}")
print(f"Structural hash: {report.structural_hash}")
```

#### Structural Hashing

```python
from markdown_ingress.core.hashing import Hasher

hasher = Hasher()
markdown = "# Title\n\nSome content"

# Content hash (exact match required)
content_hash = hasher.hash_content(markdown)

# Structural hash (same structure = same hash, even if content differs)
structural_hash = hasher.hash_structural(markdown)

# Use case: Detect document structure changes
doc1 = "# Title\n## Section\nSome content"
doc2 = "# Title\n## Section\nDifferent content"

assert hasher.hash_structural(doc1) == hasher.hash_structural(doc2)
# True - same structure!
```

#### Plugin System

```python
from markdown_ingress.core.plugin import Plugin, PluginLoader

# Define custom plugin
class MySecurityPlugin(Plugin):
    def get_patterns(self):
        return [
            r'confidential information leak',
            r'internal use only',
            r'not for distribution'
        ]

# Load plugin
loader = PluginLoader()
loader.load_plugin(MySecurityPlugin())

# Get all patterns (default + custom)
all_patterns = loader.get_all_patterns()

# Or load from directory
loader.load_from_directory("./plugins")
```

#### Benchmarking

```python
from markdown_ingress.core.benchmark import Benchmark

bench = Benchmark(model="gpt-4")

# Single URL benchmark
result = bench.run_single("https://example.com", iterations=5)

print(f"Average time: {result.avg_time_ms:.1f}ms")
print(f"Token reduction: {result.reduction_percent:.1f}%")
print(f"Original: {result.original_tokens} tokens")
print(f"Cleaned: {result.cleaned_tokens} tokens")

# Batch benchmark
urls = ["https://example.com", "https://example.org"]
results = bench.run_batch(urls, iterations=3)

# Generate report
report = bench.generate_report(results)
print(report)

# Optional extractor comparison during benchmark runs
results = bench.run_batch(
    urls,
    iterations=3,
    compare_extractors_enabled=True,
)
print(results[0].extractor_comparison)
```

#### Extractor Comparison

```python
from markdown_ingress import compare_extractors
from pathlib import Path

html = Path("page.html").read_text(encoding="utf-8")
comparison = compare_extractors(html)

print(comparison["readability"]["token_estimate"])
print(comparison["trafilatura"]["available"])
```


### Command Line Options

| Option | Description |
|--------|-------------|
| `url` | Target URL to ingest (positional) |
| `--config FILE` | Load YAML/JSON runtime config |
| `--render` | ✨ **NEW** Use render mode (Playwright for SPAs) |
| `--strict` | Enable strict security mode (default) |
| `--permissive` | Disable strict mode |
| `--model MODEL` | LLM model for token estimation (default: gpt-4) |
| `--timeout TIMEOUT` | Request timeout in seconds (default: 30) |
| `--json` | Output as JSON |
| `--save FILE` | Save output to file |
| `--output-profile PROFILE` | Apply preset output profile |
| `--extract-blocks` | Emit structured blocks |
| `--chunking-strategy {none,heading,size}` | Build native chunks |
| `--domain-policy-file FILE` | Load host-specific domain policies from JSON |
| `--show-blocks` | Show block summary in rich output |
| `--show-chunks` | Show chunk summary in rich output |
| `--show-observability` | Show stage timings and policy/cost observability |
| `--version` | Show version |

### CLI Examples

```bash
# Structured RAG-ready output
markdown-ingress ingest https://docs.example.com \
  --output-profile rag_chunkable \
  --extract-blocks \
  --chunking-strategy heading \
  --show-chunks

# Domain policy file
markdown-ingress ingest https://forum.example.com/thread \
  --domain-policy-file policies.json \
  --show-observability

# Compare extractors on saved HTML
markdown-ingress compare tests/fixtures/technical_doc.html --json

# Benchmark a URL list with extractor comparison
markdown-ingress benchmark urls.txt --iterations 5 --compare-extractors
```

---

## API Reference

### Main Function

```text
ingest(
    url: str,
    config: IngestConfig | Config | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    model: str | None = None,
    timeout: float | None = None,
    ...
) -> SafeDocument
```

### Async Function

```text
async ingest_async(
    url: str,
    config: IngestConfig | Config | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    model: str | None = None,
    timeout: float | None = None,
    ...
) -> SafeDocument
```

### Batch Functions

```text
ingest_many(
    urls: Sequence[str],
    config: IngestConfig | Config | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    max_concurrent: int = 5,
    ...
) -> BatchResult

async ingest_many_async(
    urls: Sequence[str],
    config: IngestConfig | Config | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    max_concurrent: int = 5,
    ...
) -> BatchResult
```

**Parameters:**

- `url` — Target URL to ingest
- `config` — Optional `IngestConfig` or file-based `Config`
- `mode` — Fetching mode override: `"fast"`, `"render"` or `"auto"`; defaults to `"auto"` when no config is provided
- `strict` — Optional strict security override
- `model` — Optional LLM model override (`gpt-4`, `claude`, `gpt-3.5-turbo`)
- `timeout` — Optional request timeout override in seconds
- `urls` — Sequence of target URLs for batch ingestion
- `max_concurrent` — Maximum concurrent in-flight ingestions for `ingest_many()` and `ingest_many_async()`

**Returns:** `SafeDocument` for `ingest()` / `ingest_async()`, `BatchResult` for `ingest_many()` / `ingest_many_async()`

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
    structured_blocks: list    # Optional block-level extraction output
    chunks: list               # Optional native chunk output
    security_explanation: dict # Explainability data for security decisions
    observability: dict        # Stage timings and operational telemetry
```

### Output Profiles

- `default`: conservative defaults and markdown-first output
- `llm_safe`: strict security posture with structured blocks and security metadata
- `rag_chunkable`: headings + blocks + native chunks for downstream retrieval
- `for_search`: fast extraction tuned for indexing and chunked search workflows
- `for_archive`: richer extraction with render bias and metadata retention

### Domain Policies

Domain policies can override mode, timeout, policy thresholds and output profile
per hostname. They also support granular DOM filtering:

- `blocked_tags`
- `blocked_selectors`
- `unwrap_selectors`
- `allowed_tags`

This is useful for stripping forum reply boxes, cookie walls, navigation chrome,
or preserving only semantically relevant elements on specific hosts.

### Versioned API

The server exposes versioned endpoints under `/api/v1`:

- `POST /api/v1/ingest`
- `POST /api/v1/ingest/retry`
- `POST /api/v1/ingest/batch`
- `POST /api/v1/jobs/batch`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/security/report`
- `GET /api/v1/stats`
- `GET /api/v1/health`

Batch jobs are persisted on disk, expire after a configurable TTL, and can
optionally notify a webhook on completion.

### Extractor Evaluation API

You can also compare extractor behavior directly through the API:

```bash
curl -X POST http://localhost:8000/api/v1/evaluate/extractors \
  -H "Content-Type: application/json" \
  -d '{"html":"<html><body><article><h1>Hello</h1></article></body></html>","model":"gpt-4"}'
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
    application/
        use_cases.py         # Ingestion, batch, report orchestration
        batch.py             # BatchProcessor library wrapper
    adapters/
        rendering/           # Playwright adapter boundary
        jobs/                # Persistent job queue
        webhooks/            # Webhook delivery adapters
        extractors/          # Extractor comparison adapters
    core/
        fetcher.py           # HTTP client, throttling, circuit breaker
        extractor.py         # Content extraction
        markdown.py          # HTML -> Markdown conversion
        structured.py        # Structured blocks and native chunking
        security*.py         # Prompt-injection analysis and scoring
        renderer.py          # Playwright rendering engine
        ingest_stats.py      # Shared observability counters
    api.py                   # Public Python API
    api_server.py            # FastAPI transport layer
    cli.py                   # CLI transport layer
    models.py                # SafeDocument, SecurityReport, chunk models
```

---

## Roadmap

| Version | Status | Features |
|---------|--------|----------|
| **v0.1** | ✅ Released | Fast mode, injection detection, CLI, token estimation |
| **v0.2** | ✅ Released | Playwright render mode, SPA support |
| **v0.7** | ✅ Released | ✨ Auto mode, advanced security hooks, metadata/link extraction, API + CLI |
| **v0.8** | ✅ **Current** | Output profiles, domain policies, structured blocks/chunks, persistent API jobs, observability, extractor evaluation |
| **v0.9** | 📋 Planned | More queue hardening, webhook delivery guarantees, richer extractor benchmarks, release polish |

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
- Optional (for advanced security mode):
  - `nova-hunting` — Nova semantic / LLM-assisted prompt injection detection

See [pyproject.toml](pyproject.toml) for complete dependency list.

---

## Development

### Setup

```bash
git clone https://github.com/seifreed/MarkDownIngress.git
cd MarkDownIngress
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

### Run Tests

```bash
make test                          # Full local suite, campaign/baseline excluded
make test-fast                     # Suite excluding opt-in live dataset tests
pytest tests/ -v                   # Equivalent direct command
pytest tests/ --cov=markdown_ingress
```

### Large URL Baseline

The project includes an opt-in live baseline test against the public
`ada-url/url-dataset` URL corpus. It is skipped by default because it hits
external hosts and can be very large.

```bash
make test-baseline
pytest tests/test_url_dataset_baseline.py --run-url-baseline --url-baseline-limit 250 -q
pytest tests/test_url_dataset_baseline.py --run-url-baseline --url-baseline-limit 0 -q
```

- `--url-baseline-limit 250`: quick sample baseline
- `--url-baseline-limit 0`: full dataset baseline
- Output artifacts are written under `artifacts/url_dataset_baseline/`
- Errors and warnings are recorded to JSONL instead of failing on individual bad URLs
- CI/CD includes a dedicated `URL Baseline` workflow for manual runs and scheduled checks

### Massive URL Campaign

For large-scale real-world validation, the test suite also includes an opt-in
campaign runner over the `ada-url/url-dataset` corpus. The campaign is designed
to exercise multiple ingestion profiles and options while preserving a minimum
of `50,000` distinct URLs.

```bash
make test-campaign
make test-campaign URL_CAMPAIGN_SCENARIOS=fast_default,auto_default
make test-campaign-resume URL_CAMPAIGN_RESUME_DIR=artifacts/url_dataset_campaign/campaign_YYYYMMDDTHHMMSSZ
pytest tests/test_url_dataset_campaign.py --run-url-campaign --url-campaign-limit 50000 -q
pytest tests/test_url_dataset_campaign.py --run-url-campaign --url-campaign-limit 50000 --url-campaign-scenarios fast_default,auto_default,rag_chunkable,search_profile,domain_policy_override,render_archive -q
pytest tests/test_url_dataset_campaign.py --run-url-campaign --url-campaign-limit 50000 --url-campaign-resume-dir artifacts/url_dataset_campaign/campaign_YYYYMMDDTHHMMSSZ -q
```

- `--url-campaign-limit 50000`: process at least 50K unique URLs
- `--url-campaign-scenarios ...`: choose scenario matrix explicitly
- Default concurrency is `32`
- Default batch size is `64`
- `--url-campaign-concurrency ...`: tune throughput for long runs
- `--url-campaign-batch-size ...`: tune scheduler batch size
- `--url-campaign-resume-dir ...`: resume a previous run directory after interruption/failure
- The campaign deduplicates URLs, filters to supported `http/https` inputs and spreads requests across hosts to reduce local circuit-breaker noise
- Scenarios use safer internal concurrency limits, especially for `auto` and `render`, to avoid exhausting file descriptors
- Output artifacts are written under `artifacts/url_dataset_campaign/`
- Each scenario writes its own `summary.json`, `errors.jsonl`, and `warnings.jsonl`
- The campaign root writes an aggregated `summary.json` with counts, error classes, warning classes and ingest stats
- Errors are classified so later hardening can focus on DNS, SSL, timeouts, content-type issues, rate limits and render availability
- There is also a manual GitHub Actions workflow, `URL Campaign`, for long-running remote executions with artifact upload

### Project Scope

- Python library + CLI + FastAPI server
- Fast, render and auto ingestion modes
- Batch, cache, policy, plugin and security-report workflows
- Test suite covering unit, integration and CLI/API paths

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
A: Base installs use deterministic heuristics. If you install `.[security]`, Nova-based semantic and optional LLM-assisted detection are also available.

**Q: Can I use this in production?**  
A: `0.8.0` is still marked beta. Fast mode is the most predictable path; render mode is broader but heavier. Review security scores and policy decisions for critical workflows.

**Q: How is this different from Trafilatura/Newspaper3k?**  
A: Those focus on article extraction. We add **security analysis**, **deterministic hashing**, and **LLM-specific token optimization**.

---

<p align="center">
  <sub>Built for the LLM era. Secure by default. Deterministic by design.</sub>
</p>
