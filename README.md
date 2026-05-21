<p align="center">
  <img src="https://img.shields.io/badge/MarkDownIngress-LLM%20Ingestion%20Security-blue?style=for-the-badge" alt="MarkDownIngress">
</p>

<h1 align="center">MarkDownIngress</h1>

<p align="center">
  <strong>Deterministic, injection-resistant Web → Markdown engine for LLM pipelines</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/markdown-ingress/"><img src="https://img.shields.io/pypi/v/markdown-ingress?style=flat-square&logo=pypi&logoColor=white" alt="PyPI Version"></a>
  <a href="https://pypi.org/project/markdown-ingress/"><img src="https://img.shields.io/pypi/pyversions/markdown-ingress?style=flat-square&logo=python&logoColor=white" alt="Python Versions"></a>
  <a href="https://github.com/seifreed/MarkDownIngress/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"></a>
  <a href="https://github.com/seifreed/MarkDownIngress/actions"><img src="https://img.shields.io/github/actions/workflow/status/seifreed/MarkDownIngress/ci.yml?style=flat-square&logo=github&label=CI" alt="CI Status"></a>
  <a href="https://github.com/seifreed/MarkDownIngress"><img src="https://img.shields.io/badge/output-deterministic-brightgreen?style=flat-square" alt="Deterministic"></a>
</p>

<p align="center">
  <a href="https://github.com/seifreed/MarkDownIngress/stargazers"><img src="https://img.shields.io/github/stars/seifreed/MarkDownIngress?style=flat-square" alt="GitHub Stars"></a>
  <a href="https://github.com/seifreed/MarkDownIngress/issues"><img src="https://img.shields.io/github/issues/seifreed/MarkDownIngress?style=flat-square" alt="GitHub Issues"></a>
  <a href="https://buymeacoffee.com/seifreed"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?style=flat-square&logo=buy-me-a-coffee&logoColor=white" alt="Buy Me a Coffee"></a>
</p>

---

## Overview

**MarkDownIngress** is a security-first web content ingestion engine for LLM pipelines. It fetches web pages, sanitizes HTML via Mozilla Readability, detects prompt injection patterns, converts to token-optimized Markdown, and produces deterministic output. It ships as a Python library, a FastAPI server, and a CLI.

It is **not** a recursive crawler, a full RAG framework, or a generic HTML→Markdown converter. It **is** an ingestion security boundary that flags untrusted content before it reaches a model.

### Key Features

| Feature | Description |
|---------|-------------|
| **Injection Detection** | 10+ pattern detectors with 0.0–1.0 risk scoring; optional Nova / LLM tiers |
| **Token Optimization** | 70–80% average token reduction via Readability + sanitization |
| **Deterministic Output** | Stable Markdown and SHA256 content/structural hashes in `fast` mode |
| **Fast / Render / Auto** | HTTP-only, Playwright SPA rendering, or automatic fallback |
| **Structured Blocks & Chunks** | Heading/table/code/list extraction with stable RAG chunks |
| **Domain Policies** | Per-host overrides for mode, thresholds, selectors, allowed/blocked tags |
| **Output Profiles** | `llm_safe`, `rag_chunkable`, `for_search`, `for_archive`, `default` |
| **Batch & Async** | Concurrent ingestion with in-flight dedup and per-mode stats |
| **Library + CLI + API** | `ingest()` / `markdown-ingress` / FastAPI `/api/v1/*` |

### Supported Outputs

```text
Document        SafeDocument (markdown + metadata + hashes + score + flags)
Serialization   Markdown, JSON
Security        Injection score 0.0–1.0, risk level, JSON security report
Structure       Structured blocks, native chunks, structural hash
API             FastAPI /api/v1 with persistent batch jobs + webhooks
```

---

## Installation

### From PyPI (Recommended)

```bash
pip install markdown-ingress
```

### From Source

```bash
git clone https://github.com/seifreed/MarkDownIngress.git
cd MarkDownIngress
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e .
```

### Optional Extras

```bash
pip install "markdown-ingress[all]"        # everything
pip install "markdown-ingress[render]"     # Playwright SPA rendering
pip install "markdown-ingress[security]"   # Nova advanced injection detection
pip install "markdown-ingress[api]"        # FastAPI server

# Render mode also needs a browser binary:
playwright install chromium
```

---

## Quick Start

```bash
# Ingest a single URL and print the report
markdown-ingress ingest https://example.com

# Save sanitized Markdown to a file
markdown-ingress ingest https://example.com --save example.md

# JSON output with metadata, hashes, and injection score
markdown-ingress ingest https://example.com --json --save example.json
```

Example report output:

```text
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

### Command Line Interface

```bash
# Render JavaScript-heavy SPAs with Playwright
markdown-ingress ingest https://spa-app.example.com --render --timeout 60

# RAG-ready structured output with heading-based chunks
markdown-ingress ingest https://docs.example.com \
  --output-profile rag_chunkable \
  --extract-blocks \
  --chunking-strategy heading \
  --show-chunks

# Batch a URL list into a directory of Markdown files
markdown-ingress batch urls.txt --output results/

# Compare extractors on a local HTML file (runs offline)
markdown-ingress compare tests/fixtures/technical_doc.html --json

# Benchmark token reduction across a URL list
markdown-ingress benchmark urls.txt --iterations 5 --compare-extractors
```

### Commands

| Command | Description |
|---------|-------------|
| `markdown-ingress ingest <url>` | Ingest a single URL (`text`, `--json`, `--save`) |
| `markdown-ingress batch <file>` | Process a newline-delimited URL file concurrently |
| `markdown-ingress compare <html>` | Compare Readability vs. Trafilatura on local HTML |
| `markdown-ingress benchmark <file>` | Measure latency and token reduction over a URL list |

### Key Flags (`ingest`)

| Option | Description |
|--------|-------------|
| `--render` / `--fast` | Force Playwright render mode or HTTP-only fast mode |
| `--strict` / `--permissive` | Toggle the strict security threshold (strict is default) |
| `--config FILE` | Load runtime settings from a YAML/JSON config file |
| `--model MODEL` | Token-estimation model (`gpt-4`, `claude`, `gpt-3.5-turbo`) |
| `--output-profile PROFILE` | Apply `llm_safe`, `rag_chunkable`, `for_search`, `for_archive` |
| `--extract-blocks` | Emit structured blocks (headings, tables, code, lists) |
| `--chunking-strategy {none,heading,size}` | Build stable native chunks |
| `--domain-policy-file FILE` | Load per-host policy overrides from JSON |
| `--json` / `--save FILE` | Emit JSON / write primary output to a file |
| `--show-observability` | Print stage timings and policy/cost telemetry |

---

## Python Library

### Basic Usage

```python
from markdown_ingress import ingest

doc = ingest("https://example.com", mode="fast", strict=True)

print(doc.markdown)          # Sanitized Markdown
print(doc.token_estimate)    # Token count for the chosen model
print(doc.injection_score)   # 0.0 (safe) → 1.0 (critical)
print(doc.content_hash)      # "sha256:..." for dedup/versioning
print(doc.flags)             # Security warning flags
```

### Batch and Async

```python
import asyncio
from markdown_ingress import ingest_many, ingest_async

# Concurrent batch ingestion with in-flight dedup
result = ingest_many(
    ["https://example.com/a", "https://example.com/b"],
    mode="auto",
    max_concurrent=4,
)
print(f"safe: {result.successful}/{result.total}")

# Async single ingestion
async def main():
    doc = await ingest_async("https://example.com", mode="auto")
    print(doc.metadata["title"])

asyncio.run(main())
```

### RAG-Ready Structured Output

```python
from markdown_ingress import ingest

doc = ingest(
    "https://docs.example.com/guide",
    mode="fast",
    output_profile="rag_chunkable",
    extract_blocks=True,
    chunking_strategy="heading",
)

print(doc.structured_blocks[0]["block_type"])
print(doc.chunks[0]["chunk_id"])
```

### Security Report API

```python
from markdown_ingress import generate_security_report

report = generate_security_report("https://suspicious-site.example.com")
report.save("security_report.json")

print(report.injection_score)            # numeric risk score
print(report.risk_level)                 # SAFE / LOW / MEDIUM / HIGH / CRITICAL
print(report.token_reduction_percent)    # token savings %
print(report.pattern_matches)            # matched injection patterns
```

### Domain-Specific Hardening

```python
from markdown_ingress import DomainPolicy, ingest

doc = ingest(
    "https://forum.example.com/thread",
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
print(doc.metadata["domain_policy"])
```

More runnable scripts live in [`examples/`](examples/) — see
[`library_usage.py`](examples/library_usage.py) and
[`library_batch_async.py`](examples/library_batch_async.py).

---

## Security Model

### Injection Detection Patterns

| Pattern | Weight | Example |
|---------|--------|---------|
| Instruction Override | 0.8 | "ignore previous instructions" |
| Secret Extraction | 0.9 | "reveal secret keys" |
| Mode Switching | 0.7 | "enable developer mode" |
| System Prompt Access | 0.6 | "reveal system prompt" |
| Policy Override | 0.8 | "override policy settings" |
| Model Manipulation | 0.5 | "you are ChatGPT" |

### Risk Levels

| Score | Level | Action |
|-------|-------|--------|
| 0.0 – 0.2 | **SAFE** | Content appears safe |
| 0.2 – 0.4 | **LOW** | Review recommended |
| 0.4 – 0.6 | **MEDIUM** | Manual review required |
| 0.6 – 0.8 | **HIGH** | Use with caution |
| 0.8 – 1.0 | **CRITICAL** | Blocking recommended |

Base installs use deterministic heuristics. Install `[security]` to add Nova
semantic detection, and set `ANTHROPIC_API_KEY` with `--use-llm` for the
optional LLM-assisted tier.

---

## FastAPI Server

```bash
pip install "markdown-ingress[api]"
uvicorn markdown_ingress.api_server:app --port 8000
```

Versioned endpoints under `/api/v1`:

```text
POST /api/v1/ingest              Single URL ingestion
POST /api/v1/ingest/batch        Synchronous batch
POST /api/v1/jobs/batch          Persistent batch job (TTL + optional webhook)
GET  /api/v1/jobs/{job_id}       Job status / result
POST /api/v1/security/report     Security report for a URL
GET  /api/v1/stats               Process-level ingest stats
GET  /api/v1/health              Health check
```

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","mode":"fast","strict":true}'
```

---

## Requirements

- Python 3.13 or 3.14
- Core: `httpx`, `selectolax`, `readability-lxml`, `markdownify`, `tiktoken`
- Optional: `playwright` (render), `nova-hunting` (security), `fastapi` (api)
- See [pyproject.toml](pyproject.toml) for the complete dependency list

---

## Development

```bash
git clone https://github.com/seifreed/MarkDownIngress.git
cd MarkDownIngress
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
playwright install chromium

make test          # full local suite (campaign/baseline excluded)
make test-fast     # suite excluding opt-in live dataset tests
ruff check markdown_ingress tests
black --check markdown_ingress tests
mypy markdown_ingress
bandit -r markdown_ingress
```

Every bug fix must include a regression test that fails before the fix and
passes after it. `ruff`, `black --check`, `mypy`, and `bandit` must pass
before code is considered complete.

---

## Release

Releases are tag driven. Update the package version in `pyproject.toml` and
`markdown_ingress/__init__.py`, commit the change, then create and push a `v*`
tag:

```bash
git tag v0.8.0
git push origin v0.8.0
```

The publish workflow builds the wheel and source distribution, checks them with
`twine`, creates or updates the matching GitHub Release, and uploads `dist/*` as
release assets. If `PYPI_TOKEN` is configured in GitHub Secrets, the same
workflow also publishes the package to PyPI.

---

## Support the Project

If this project is useful in your workflows, you can support development:

<a href="https://buymeacoffee.com/seifreed" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50">
</a>

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

**Attribution**
- Author: **Marc Rivero López** | [@seifreed](https://github.com/seifreed)
- Repository: [github.com/seifreed/MarkDownIngress](https://github.com/seifreed/MarkDownIngress)

---

<p align="center">
  <sub>Built for the LLM era. Secure by default. Deterministic by design.</sub>
</p>
