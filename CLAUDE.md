# Repository Agent Instructions

This file provides guidance to coding agents when working with code in this repository.

## Project Overview

**MarkDownIngress** is a security-first web content ingestion engine for LLM pipelines. It fetches web pages, sanitizes HTML via Mozilla Readability, detects prompt injection patterns, converts to token-optimized Markdown, and produces deterministic output. Exposed as Python library (`api.py`), FastAPI REST server (`api_server.py`), and CLI (`cli.py`).

## Commands

```bash
# Run all tests with coverage
make test

# Run fast tests (skip baseline/network/campaign markers — no external URLs)
make test-fast

# Run a single test file
python -m pytest tests/test_security.py -v

# Run specific test
python -m pytest tests/test_security.py::test_safe_content -v

# Run tests matching a keyword
python -m pytest -k "test_cache" -v

# Lint
ruff check markdown_ingress tests

# Format
black markdown_ingress tests

# Type check
mypy markdown_ingress

# Security scan
bandit -r markdown_ingress

# Full local quality gate for code changes
ruff check markdown_ingress tests
black --check markdown_ingress tests
mypy markdown_ingress
bandit -r markdown_ingress
python -m pytest

# Install for development
pip install -e ".[dev]"
playwright install chromium  # For render mode
```

## Architecture

### Pipeline Flow

```
URL → Fetcher → (Renderer) → Extractor → Normalizer → SecurityAnalyzer → MarkdownConverter → StructuredBlocks → TokenEstimator → MetadataExtractor → LinkAnalyzer → Cache → SafeDocument
```

The `IngestOrchestrator` (`core/orchestrator.py`) coordinates all pipeline stages using dependency injection.

### Layer Responsibilities

| Layer | Location | Purpose |
|---|---|---|
| Public API | `api.py`, `api_facade.py` | `ingest()` / `ingest_async()` / `ingest_many()` entry points |
| Application | `application/use_cases.py` | Mode selection (fast/render/auto), fallback handling, retry logic |
| Orchestrator | `core/orchestrator.py` | Wires together all pipeline components |
| Core components | `core/` | Each stage is a dedicated class (Fetcher, Extractor, Normalizer, etc.) |
| Adapters | `adapters/` | Pluggable implementations: PlaywrightRenderer, SQLiteJobQueue, HTTPNotifier |
| API server | `api_server*.py` | FastAPI server with versioned endpoints `/api/v1/*` |
| CLI | `cli*.py` | Rich-based terminal output |

### Interfaces

All core components implement protocols defined in `core/interfaces.py` (`IFetcher`, `IRenderer`, `IExtractor`, `INormalizer`, `ICacheBackend`, `IWebhookNotifier`, `IJobQueue`). This enables dependency injection in `IngestOrchestrator.__init__`.

### Key Models

- `SafeDocument` — Main output: markdown, metadata, token_estimate, content_hash, injection_score, flags
- `IngestConfig` — Pipeline configuration (mode, timeout, policy, etc.)
- `RenderConfig` — Playwright-specific settings
- `DomainPolicy` — Host-specific overrides

## Conventions

### Required Quality Gate

Every bug fix must include regression tests that fail before the fix and pass after it, unless
the bug is impossible to exercise automatically. In that exceptional case, document the reason
in the commit or PR notes and add the closest practical coverage.

Before code is considered complete, the full relevant test suite and these tools must pass:
`ruff`, `black --check`, `mypy`, and `bandit`.

Do not hide failures by relaxing, disabling, or suppressing tool errors in `pyproject.toml`.
Fix the underlying issue instead. Any change to tool configuration must be justified by a real
project-wide policy change, not by a local failure.

Follow clean code and clean architecture principles: keep responsibilities small and explicit,
preserve dependency direction, avoid infrastructure leakage into core logic, prefer clear names,
and keep changes scoped to the behavior being modified.

### Processor Pattern

Each pipeline stage has a single primary method:
```python
Fetcher.fetch() / fetch_sync()
Extractor.extract()
Normalizer.normalize()
MarkdownConverter.convert()
SecurityAnalyzer.analyze()
TokenEstimator.estimate()
Hasher.hash() / structural_hash()
```

### Dataclass Configuration

Never add long parameter lists. Use dataclasses:
```python
@dataclass
class IngestConfig:
    mode: Literal["fast", "render", "auto"] = "auto"
    timeout: float = 30.0
    # ...
```

### Dependency Injection

```python
class IngestOrchestrator:
    def __init__(self, extractor: IExtractor | None = None, ...):
        self.extractor = extractor or Extractor()
```

### Sync/Async Parity

Every public API has both sync and async variants. Sync versions delegate to `asyncio.run(async_version(...))`. Keep them in sync.

### Custom Exceptions

Use the specific exception hierarchy:
- `UnsupportedContentTypeError(ValueError)` — Non-HTML content
- `DomainCircuitOpenError(RuntimeError)` — Circuit breaker open
- `PolicyBlockedError(RuntimeError)` — Policy enforcement

### Test Markers

- `@pytest.mark.baseline` — Tests hitting real external URLs (~250 URLs); skipped by `make test-fast`
- `@pytest.mark.network` — Any test requiring network; also skipped by fast target
- `@pytest.mark.campaign` — Large-scale campaign tests (50K URLs); opt-in only

Tag new tests that hit the network appropriately.

### Optional Dependencies

Guard imports of optional dependencies:
```python
try:
    from nova_hunting import NovaDetector
    NOVA_AVAILABLE = True
except ImportError:
    NOVA_AVAILABLE = False
```

Extras: `[render]` (Playwright), `[security]` (Nova AI), `[api]` (FastAPI).

### Line Length

100 characters (ruff + black both configured to 100).
