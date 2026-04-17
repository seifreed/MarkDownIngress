# Copilot Instructions for MarkDownIngress

## Project Overview

**MarkDownIngress** is a security-first web content ingestion engine for LLM pipelines. It fetches web pages, sanitizes HTML via Mozilla Readability, detects prompt injection (10+ patterns + optional Nova AI), converts to token-optimized Markdown, and produces deterministic output suitable for LLM consumption.

Exposed as three interfaces: Python library (`api.py`), FastAPI REST server (`api_server.py`), and CLI (`cli.py`).

---

## Commands

```bash
# Run all tests with coverage
make test

# Run fast tests (skip @baseline, @network, @campaign markers — no external URLs)
make test-fast

# Run a single test
python -m pytest tests/test_security.py::test_safe_content -v

# Run all tests in a file
python -m pytest tests/test_security.py -v

# Run tests matching a keyword
python -m pytest -k "test_cache" -v

# Lint
ruff check markdown_ingress tests

# Format
black markdown_ingress tests

# Type check
mypy markdown_ingress
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — async test functions are automatically run without decorators.

---

## Architecture

The core is a sequential processing pipeline coordinated by `IngestOrchestrator` (`core/orchestrator.py`):

```
URL → Fetcher → (Renderer) → Extractor → Normalizer → SecurityAnalyzer → MarkdownConverter → StructuredBlocks → TokenEstimator → MetadataExtractor → LinkAnalyzer → Cache → SafeDocument
```

**Layer responsibilities:**

| Layer | Location | Purpose |
|---|---|---|
| Public API | `api.py`, `api_facade.py` | `ingest()` / `ingest_async()` / `ingest_many()` entry points |
| Application | `application/use_cases.py` | Mode selection (fast/render/auto), fallback handling, retry logic |
| Orchestrator | `core/orchestrator.py` | Wires together all pipeline components |
| Core components | `core/` | Each stage is a dedicated class (see Processor Pattern below) |
| Adapters | `adapters/` | Pluggable implementations of protocols (Playwright, SQLiteJobQueue, HTTPNotifier) |
| API server | `api_server*.py` | FastAPI server; job polling via `SQLiteJobQueue` |
| CLI | `cli*.py` | Rich-based terminal output |

**Interfaces (`core/interfaces.py`)** define protocols (`IFetcher`, `IRenderer`, `IExtractor`, `INormalizer`, `ICacheBackend`, etc.) that all concrete implementations satisfy — used for dependency injection in `IngestOrchestrator`.

---

## Key Conventions

### Processor Pattern
Each pipeline stage is a class with a single primary method:

```python
Fetcher.fetch() / fetch_sync()
Extractor.extract()
Normalizer.normalize()
MarkdownConverter.convert()
SecurityAnalyzer.analyze()
TokenEstimator.estimate()
Hasher.hash() / structural_hash()
MetadataExtractor.extract()
LinkAnalyzer.extract()
```

Private helpers are `_snake_case` static or instance methods on the same class.

### Dataclass Configuration (not kwargs sprawl)
All configuration uses dataclasses. Never add long parameter lists to pipeline methods:

```python
@dataclass
class IngestConfig:
    mode: Literal["fast", "render", "auto"] = "auto"
    strict: bool = True
    model: str = "gpt-4"
    timeout: float = 30.0
    # ...

@dataclass
class RenderConfig:
    timeout: float = 30.0
    stealth: bool = False
    # ...
```

### Dependency Injection in Orchestrator
`IngestOrchestrator.__init__` accepts optional components defaulting to standard implementations:

```python
class IngestOrchestrator:
    def __init__(self, extractor: IExtractor | None = None, ...):
        self.extractor = extractor or Extractor()
```

Always follow this pattern when adding new injectable components.

### Sync/Async Parity
Every public API function has both sync and async variants. Sync versions delegate to `asyncio.run(async_version(...))`. Keep them in sync when adding new features.

### Custom Exception Types
Use the specific exception hierarchy — don't raise generic `RuntimeError` or `ValueError`:

```python
UnsupportedContentTypeError(ValueError)   # Non-HTML content
DomainCircuitOpenError(RuntimeError)      # Circuit breaker open
PolicyBlockedError(RuntimeError)          # Policy enforcement
```

### Models
All data flowing through the pipeline is typed with dataclasses from `models.py` and `config_models.py`. The canonical output type is `SafeDocument`. Use `field(default_factory=list)` / `field(default_factory=dict)` for mutable defaults.

### Test Markers
- `@pytest.mark.baseline` — Tests hitting real external URLs (~250 URLs); skipped by `make test-fast`
- `@pytest.mark.network` — Any test requiring network; also skipped by fast target
- `@pytest.mark.campaign` — Large-scale campaign tests (50K URLs); opt-in only

Tag new tests that hit the network with the appropriate marker.

### Optional Feature Groups
Dependencies are grouped as extras: `[render]` (Playwright), `[security]` (Nova AI), `[api]` (FastAPI). Guard imports of optional dependencies:

```python
try:
    from nova_hunting import NovaDetector
    NOVA_AVAILABLE = True
except ImportError:
    NOVA_AVAILABLE = False
```

### Line Length
100 characters (ruff + black both configured to 100).
