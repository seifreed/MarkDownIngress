"""Tests for batch processing"""

import asyncio
import gc
import importlib
import pickle
import queue as queue_module
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import markdown_ingress.api as public_api
from markdown_ingress import ingest_async, ingest_many, ingest_many_async
from markdown_ingress.adapters.cache.memory import MemoryCache
from markdown_ingress.api_runtime import UNSET, resolve_batch_api_options
from markdown_ingress.application.batch import BatchProcessor
from markdown_ingress.application.subprocess_runner import (
    _poll_subprocess_queue,
    _select_execution_strategy,
)
from markdown_ingress.application.use_cases import BatchIngestUseCase, IngestUseCase
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.config import Config
from markdown_ingress.core.inflight import InFlightRegistry
from markdown_ingress.core.orchestrator import IngestOrchestrator
from markdown_ingress.models import FetchResult, SafeDocument
from markdown_ingress.shared_results import BatchResult


class _CopyBatchExceptionError(Exception):
    pass


@pytest.fixture(scope="module")
def local_servers():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = b"<html><body><h1>Batch Test</h1><p>Content.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    servers = []
    urls = []
    for _ in range(2):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        urls.append(f"http://127.0.0.1:{server.server_address[1]}")

    yield urls

    for server in servers:
        server.shutdown()


@pytest.mark.asyncio
async def test_batch_processor_basic(local_servers):
    """Test basic batch processing"""
    urls = local_servers

    processor = BatchProcessor(mode="fast", timeout=15.0, max_concurrent=2)
    result = await processor.process_batch_async(urls)

    assert result.total == 2
    assert result.successful == 2
    assert result.failed == 0
    assert len(result.documents) == 2
    assert result.success_rate == 100.0


def test_batch_processor_defaults_to_auto_mode():
    assert BatchProcessor().mode == "auto"


def test_batch_processor_preserves_base_config_without_explicit_overrides():
    base = IngestConfig(mode="render", strict=False, model="custom", timeout=99.0)

    processor = BatchProcessor(base_config=base)
    config = processor._build_config()

    assert config.mode == "render"
    assert config.strict is False
    assert config.model == "custom"
    assert config.timeout == 99.0


def test_shared_fetcher_manager_does_not_close_fetcher_used_by_concurrent_request():
    started_a = threading.Event()
    b_done = threading.Event()
    closed_user_agents: list[str] = []

    class RaceFetcher:
        def __init__(self, config):
            self.user_agent = config.fetcher_user_agent
            self.closed = False

        def fetch_sync(self, url: str):
            if self.user_agent == "UA-A":
                started_a.set()
                assert b_done.wait(2.0)
                if self.closed:
                    raise RuntimeError("fetcher A closed during fetch")
            else:
                b_done.set()
            return FetchResult(
                html=(
                    "<html><body><article>"
                    f"<h1>{self.user_agent}</h1><p>content content content</p>"
                    "</article></body></html>"
                ),
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
            )

        def close(self):
            self.closed = True
            closed_user_agents.append(self.user_agent)

    use_case = IngestUseCase(
        fetcher_factory=lambda config: RaceFetcher(config),
        playwright_available=False,
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(
                lambda: use_case.execute(
                    "https://unit.test/a",
                    IngestConfig(
                        mode="fast",
                        fetcher_user_agent="UA-A",
                        extract_metadata=False,
                        extract_links=False,
                    ),
                )
            )
            assert started_a.wait(2.0)
            future_b = pool.submit(
                lambda: use_case.execute(
                    "https://unit.test/b",
                    IngestConfig(
                        mode="fast",
                        fetcher_user_agent="UA-B",
                        extract_metadata=False,
                        extract_links=False,
                    ),
                )
            )

            doc_a = future_a.result(timeout=5.0)
            doc_b = future_b.result(timeout=5.0)

        assert "UA-A" in doc_a.markdown
        assert "UA-B" in doc_b.markdown
        assert closed_user_agents == []
    finally:
        use_case.close()


def test_automatic_fetcher_user_agent_is_stable_across_urls_same_use_case():
    created_user_agents: list[str] = []

    class TrackingFetcher:
        def __init__(self, config):
            self.user_agent = config.fetcher_user_agent
            created_user_agents.append(self.user_agent)

        def fetch_sync(self, url: str):
            return FetchResult(
                html=(
                    "<html><body><article>"
                    f"<h1>{url}</h1><p>content content content</p>"
                    "</article></body></html>"
                ),
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
            )

    use_case = IngestUseCase(
        fetcher_factory=lambda config: TrackingFetcher(config),
        playwright_available=False,
    )
    config = IngestConfig(mode="fast", extract_metadata=False, extract_links=False)

    try:
        use_case.execute("https://unit.test/path0", config)
        use_case.execute("https://unit.test/path1", config)

        assert len(created_user_agents) == 1
        assert created_user_agents[0]
    finally:
        use_case.close()


def test_batch_processor_applies_only_explicit_overrides_on_base_config():
    base = IngestConfig(mode="render", strict=False, model="custom", timeout=99.0)

    processor = BatchProcessor(
        mode="fast",
        timeout=10.0,
        base_config=base,
        explicit_overrides=frozenset({"mode", "timeout"}),
    )
    config = processor._build_config()

    assert config.mode == "fast"
    assert config.strict is False
    assert config.model == "custom"
    assert config.timeout == 10.0


def test_batch_processor_renderer_availability_uses_playwright_constant(monkeypatch):
    import markdown_ingress.adapters.rendering.playwright_renderer as renderer_module
    import markdown_ingress.application.batch as batch_module

    original = renderer_module.PLAYWRIGHT_INSTALLED
    try:
        monkeypatch.setattr(renderer_module, "PLAYWRIGHT_INSTALLED", False)
        reloaded_batch = importlib.reload(batch_module)

        processor = reloaded_batch.BatchProcessor(mode="auto")

        assert reloaded_batch.RENDERER_AVAILABLE is False
        assert processor._batch_use_case.ingest_use_case.playwright_available is False
    finally:
        monkeypatch.setattr(renderer_module, "PLAYWRIGHT_INSTALLED", original)
        importlib.reload(batch_module)


def test_batch_processor_sync(local_servers):
    """Test synchronous batch processing"""
    urls = [local_servers[0]]

    processor = BatchProcessor(mode="fast", timeout=10.0)
    result = processor.process_batch(urls)

    assert result.successful == 1
    assert len(result.documents) == 1


@pytest.mark.asyncio
async def test_batch_processor_sync_rejects_active_event_loop():
    processor = BatchProcessor(mode="fast", timeout=5.0)

    with pytest.raises(RuntimeError, match="ingest_many_async\\(\\) instead"):
        processor.process_batch(["https://example.com"])


@pytest.mark.asyncio
async def test_batch_with_errors(local_servers):
    """Test batch processing with some failures"""
    urls = [
        local_servers[0],
        "http://invalid-url-that-does-not-exist-12345.com",
    ]

    processor = BatchProcessor(mode="fast", timeout=5.0)
    result = await processor.process_batch_async(urls)

    assert result.total == 2
    assert result.successful == 1
    assert result.failed == 1
    assert len(result.errors) == 1
    assert result.errors[0].url == "http://invalid-url-that-does-not-exist-12345.com"


@pytest.mark.asyncio
async def test_batch_processor_custom_process_url_none_is_counted_as_failure():
    class NoneBatchProcessor(BatchProcessor):
        async def process_url(self, url: str):
            return None

    processor = NoneBatchProcessor(mode="fast", timeout=5.0)
    result = await processor.process_batch_async(["https://example.com"])

    assert result.total == 1
    assert result.successful == 0
    assert result.failed == 1
    assert result.documents == [None]
    assert len(result.errors) == 1
    assert result.errors[0].url == "https://example.com"
    assert result.errors[0].error_type == "TypeError"
    assert "returned None instead of SafeDocument" in result.errors[0].error


@pytest.mark.asyncio
async def test_batch_processor_custom_process_url_invalid_type_is_counted_as_failure():
    class InvalidBatchProcessor(BatchProcessor):
        async def process_url(self, url: str):
            return {"not": "doc"}

    processor = InvalidBatchProcessor(mode="fast", timeout=5.0)
    result = await processor.process_batch_async(["https://example.com"])

    assert result.total == 1
    assert result.successful == 0
    assert result.failed == 1
    assert result.documents == [None]
    assert len(result.errors) == 1
    assert result.errors[0].url == "https://example.com"
    assert result.errors[0].error_type == "TypeError"
    assert "returned dict instead of SafeDocument" in result.errors[0].error


@pytest.mark.asyncio
async def test_batch_processor_custom_process_url_rejects_zero_concurrency():
    class ValidBatchProcessor(BatchProcessor):
        async def process_url(self, url: str):
            return SafeDocument(
                markdown="ok",
                metadata={"url": url},
                token_estimate=1,
                injection_score=0.0,
                content_hash="sha256:x",
            )

    processor = ValidBatchProcessor(max_concurrent=0)

    with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
        await processor.process_batch_async(["https://example.com"])


@pytest.mark.parametrize("bad_max_concurrent", [True, 1.5, "2"])
@pytest.mark.asyncio
async def test_batch_processor_custom_process_url_rejects_non_integer_concurrency(
    bad_max_concurrent,
):
    class ValidBatchProcessor(BatchProcessor):
        async def process_url(self, url: str):
            return SafeDocument(
                markdown="ok",
                metadata={"url": url},
                token_estimate=1,
                injection_score=0.0,
                content_hash="sha256:x",
            )

    processor = ValidBatchProcessor(max_concurrent=bad_max_concurrent)

    with pytest.raises(ValueError, match="max_concurrent must be an int"):
        await processor.process_batch_async(["https://example.com"])


def test_ingest_continues_when_cache_backend_fails():
    class BoomCache:
        def get(self, key):
            raise RuntimeError("cache down")

        def set(self, key, document, ttl=None):
            raise RuntimeError("cache down")

        def delete(self, key):
            pass

        def clear(self):
            pass

        def exists(self, key):
            return False

    class FakeFetcher:
        def fetch_sync(self, url):
            return FetchResult(
                html="<html><body><main>Hello batch</main></body></html>",
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
            )

    use_case = IngestUseCase(fetcher_factory=lambda config: FakeFetcher())
    config = IngestConfig(
        mode="fast", cache=BoomCache(), extract_metadata=False, extract_links=False
    )

    doc = use_case.execute("https://unit.test/cache-fail", config)

    assert doc.metadata["cache_hit"] is False
    assert "Hello batch" in doc.markdown


def test_ingest_recovers_from_corrupt_cache_entry():
    class CorruptCache:
        def __init__(self):
            self.deleted_keys: list[str] = []

        def get(self, key):
            return "corrupt"

        def set(self, key, document, ttl=None):
            pass

        def delete(self, key):
            self.deleted_keys.append(key)

        def clear(self):
            pass

        def exists(self, key):
            return False

    class FakeFetcher:
        def fetch_sync(self, url):
            return FetchResult(
                html="<html><body><main>Hello recovered cache</main></body></html>",
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
            )

    cache = CorruptCache()
    use_case = IngestUseCase(fetcher_factory=lambda config: FakeFetcher())
    config = IngestConfig(mode="fast", cache=cache, extract_metadata=False, extract_links=False)

    doc = use_case.execute("https://unit.test/cache-corrupt", config)

    assert doc.metadata["cache_hit"] is False
    assert "Hello recovered cache" in doc.markdown
    assert len(cache.deleted_keys) == 1


@pytest.mark.asyncio
async def test_batch_falls_back_to_local_execution_for_custom_fetcher(monkeypatch):
    class FakeFetcher:
        def fetch_sync(self, url):
            return FetchResult(
                html="<html><body><main>Hello batch</main></body></html>",
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
            )

    use_case = IngestUseCase(fetcher_factory=lambda config: FakeFetcher())
    batch_use_case = BatchIngestUseCase(ingest_use_case=use_case)
    config = IngestConfig(mode="fast", extract_metadata=False, extract_links=False)
    single_doc = use_case.execute("ftp://ignored.example/path", config)

    async def fail_if_isolated(_prepared):
        raise AssertionError("batch should not use subprocess isolation for custom dependencies")

    monkeypatch.setattr(batch_use_case, "_execute_item_isolated", fail_if_isolated)

    result = await batch_use_case.execute(
        ["ftp://ignored.example/path"],
        lambda: config,
        max_concurrent=1,
    )

    assert result.successful == 1
    assert result.failed == 0
    assert result.documents[0] is not None
    assert result.documents[0].markdown == single_doc.markdown


@pytest.mark.asyncio
async def test_batch_continues_when_cache_backend_fails():
    class BoomCache:
        def get(self, key):
            raise RuntimeError("cache down")

        def set(self, key, document, ttl=None):
            raise RuntimeError("cache down")

        def delete(self, key):
            pass

        def clear(self):
            pass

        def exists(self, key):
            return False

    class FakeFetcher:
        def fetch_sync(self, url):
            return FetchResult(
                html="<html><body><main>Hello batch</main></body></html>",
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
            )

    use_case = IngestUseCase(fetcher_factory=lambda config: FakeFetcher())
    batch_use_case = BatchIngestUseCase(ingest_use_case=use_case)
    config = IngestConfig(
        mode="fast", cache=BoomCache(), extract_metadata=False, extract_links=False
    )

    result = await batch_use_case.execute(
        ["https://unit.test/cache-fail"],
        lambda: config,
        max_concurrent=1,
    )

    assert result.successful == 1
    assert result.failed == 0
    assert result.documents[0] is not None


@pytest.mark.asyncio
async def test_batch_temp_screenshot_results_are_not_cached(monkeypatch):
    monkeypatch.setattr(
        "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
        lambda url, **_kwargs: url,
    )

    cache = MemoryCache()
    captured_paths: list[str] = []

    class FakeRenderer:
        calls = 0

        def __init__(self, config):
            self.config = config

        def render_sync(self, url: str):
            type(self).calls += 1
            time.sleep(0.05)
            screenshot_path = self.config.screenshot
            assert isinstance(screenshot_path, str)
            Path(screenshot_path).write_bytes(b"temporary screenshot bytes")
            captured_paths.append(screenshot_path)
            return FetchResult(
                html=(
                    "<html><body><article><h1>Render</h1>"
                    f"<p>call {type(self).calls}</p></article></body></html>"
                ),
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
                metadata={"screenshot_path": screenshot_path},
            )

    use_case = IngestUseCase(playwright_available=True)
    use_case.renderer_factory = lambda config: FakeRenderer(config)
    batch_use_case = BatchIngestUseCase(ingest_use_case=use_case)
    config = IngestConfig(
        mode="render",
        screenshot=True,
        cache=cache,
        extract_metadata=False,
        extract_links=False,
    )

    try:
        result = await batch_use_case.execute(
            [
                "https://unit.test/temp-screenshot-cache",
                "https://unit.test/temp-screenshot-cache",
            ],
            lambda: config,
            max_concurrent=2,
        )

        assert result.successful == 2
        assert result.failed == 0
        assert FakeRenderer.calls == 2
        assert result.documents[0] is not None
        assert result.documents[1] is not None
        assert result.documents[0].metadata["cache_hit"] is False
        assert result.documents[1].metadata["cache_hit"] is False
        assert result.documents[0].screenshot_path != result.documents[1].screenshot_path
        assert set(captured_paths) == {
            result.documents[0].screenshot_path,
            result.documents[1].screenshot_path,
        }
    finally:
        for path in captured_paths:
            Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_batch_explicit_screenshot_path_bypasses_cache_and_inflight(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
        lambda url, **_kwargs: url,
    )

    cache = MemoryCache()
    screenshot_path = tmp_path / "explicit-batch.png"

    class FakeRenderer:
        calls = 0

        def __init__(self, config):
            self.config = config

        def render_sync(self, url: str):
            type(self).calls += 1
            time.sleep(0.05)
            assert self.config.screenshot == str(screenshot_path)
            screenshot_path.write_bytes(f"batch shot {type(self).calls}".encode())
            return FetchResult(
                html=(
                    "<html><body><article><h1>Render</h1>"
                    f"<p>call {type(self).calls}</p></article></body></html>"
                ),
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
                metadata={"screenshot_path": str(screenshot_path)},
            )

    use_case = IngestUseCase(playwright_available=True)
    use_case.renderer_factory = lambda config: FakeRenderer(config)
    batch_use_case = BatchIngestUseCase(ingest_use_case=use_case)
    config = IngestConfig(
        mode="render",
        screenshot=str(screenshot_path),
        cache=cache,
        extract_metadata=False,
        extract_links=False,
    )

    result = await batch_use_case.execute(
        [
            "https://unit.test/explicit-screenshot-cache",
            "https://unit.test/explicit-screenshot-cache",
        ],
        lambda: config,
        max_concurrent=2,
    )

    assert result.successful == 2
    assert result.failed == 0
    assert FakeRenderer.calls == 2
    assert result.documents[0] is not None
    assert result.documents[1] is not None
    assert result.documents[0].metadata["cache_hit"] is False
    assert result.documents[1].metadata["cache_hit"] is False
    assert result.documents[0].metadata["inflight_deduplicated"] is False
    assert result.documents[1].metadata["inflight_deduplicated"] is False


@pytest.mark.asyncio
async def test_batch_recovers_from_corrupt_cache_entry():
    class CorruptCache:
        def __init__(self):
            self.deleted_keys: list[str] = []

        def get(self, key):
            return "corrupt"

        def set(self, key, document, ttl=None):
            pass

        def delete(self, key):
            self.deleted_keys.append(key)

        def clear(self):
            pass

        def exists(self, key):
            return False

    class FakeFetcher:
        def fetch_sync(self, url):
            return FetchResult(
                html="<html><body><main>Hello repaired batch cache</main></body></html>",
                url=url,
                status_code=200,
                final_url=url,
                headers={"content-type": "text/html"},
                timing_ms=1.0,
            )

    cache = CorruptCache()
    use_case = IngestUseCase(fetcher_factory=lambda config: FakeFetcher())
    batch_use_case = BatchIngestUseCase(ingest_use_case=use_case)
    config = IngestConfig(mode="fast", cache=cache, extract_metadata=False, extract_links=False)

    result = await batch_use_case.execute(
        ["https://unit.test/cache-corrupt"],
        lambda: config,
        max_concurrent=1,
    )

    assert result.successful == 1
    assert result.failed == 0
    assert result.documents[0] is not None
    assert "Hello repaired batch cache" in result.documents[0].markdown
    assert len(cache.deleted_keys) == 1


@pytest.mark.asyncio
async def test_batch_falls_back_to_local_execution_when_main_is_not_importable(monkeypatch):
    batch_use_case = BatchIngestUseCase(ingest_use_case=IngestUseCase(playwright_available=False))
    local_calls = {"count": 0}

    async def fake_local(_prepared):
        local_calls["count"] += 1
        return SafeDocument(
            markdown="# local",
            metadata={"url": "https://unit.test/interactive"},
            token_estimate=1,
            content_hash="sha256:test",
            injection_score=0.0,
        )

    async def fail_if_isolated(_prepared):
        raise AssertionError(
            "batch should not use subprocess isolation without importable __main__"
        )

    monkeypatch.setattr(
        "markdown_ingress.application.subprocess_runner._main_module_file", lambda: None
    )
    monkeypatch.setattr(batch_use_case, "_execute_item_in_process", fake_local)
    monkeypatch.setattr(batch_use_case, "_execute_item_isolated", fail_if_isolated)

    result = await batch_use_case.execute(
        ["https://unit.test/interactive"],
        lambda: IngestConfig(mode="fast"),
        max_concurrent=1,
    )

    assert result.successful == 1
    assert result.failed == 0
    assert local_calls["count"] == 1


def test_uses_default_runtime_dependencies_rejects_injected_inflight_registry():
    use_case = IngestUseCase(orchestrator=IngestOrchestrator(inflight_registry=InFlightRegistry()))

    assert use_case.uses_default_runtime_dependencies() is False


def test_batch_selects_local_strategy_for_injected_inflight_registry():
    batch_use_case = BatchIngestUseCase(
        ingest_use_case=IngestUseCase(
            orchestrator=IngestOrchestrator(inflight_registry=InFlightRegistry())
        )
    )

    strategy, reason = _select_execution_strategy(batch_use_case.ingest_use_case)

    assert strategy == "local"
    assert reason == "custom runtime dependencies"


@pytest.mark.asyncio
async def test_poll_subprocess_queue_reads_payload_after_process_exit_even_if_empty_lies():
    document = SafeDocument(
        markdown="ok",
        metadata={"url": "https://example.com"},
        token_estimate=1,
        injection_score=0.0,
        content_hash="hash",
    )

    class FinishedProcess:
        def __init__(self):
            self.joins = 0

        def is_alive(self):
            return False

        def join(self, timeout=None):
            self.joins += 1

    class RaceQueue:
        def __init__(self):
            self.calls = 0

        def empty(self):
            return True

        def get_nowait(self):
            self.calls += 1
            if self.calls == 1:
                raise queue_module.Empty
            return ("result", document)

        def get(self, timeout=None):
            return self.get_nowait()

    process = FinishedProcess()
    result = await _poll_subprocess_queue(process, RaceQueue(), "https://example.com")

    assert result is document
    assert process.joins >= 1


@pytest.mark.asyncio
async def test_poll_subprocess_queue_rejects_non_document_result():
    class FinishedProcess:
        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    class PayloadQueue:
        def get_nowait(self):
            return ("result", {"not": "a document"})

        def get(self, timeout=None):
            return self.get_nowait()

    with pytest.raises(RuntimeError, match="non-document result.*dict"):
        await _poll_subprocess_queue(FinishedProcess(), PayloadQueue(), "https://example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "not-a-tuple",
        ("exception_payload", "not-a-dict"),
        ("exception_payload", {"type": "RuntimeError"}),
        ("exception_payload", {"message": "boom"}),
    ],
)
async def test_poll_subprocess_queue_rejects_malformed_payloads(payload):
    class FinishedProcess:
        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    class PayloadQueue:
        def get_nowait(self):
            return payload

        def get(self, timeout=None):
            return self.get_nowait()

    with pytest.raises(RuntimeError, match="malformed"):
        await _poll_subprocess_queue(FinishedProcess(), PayloadQueue(), "https://example.com")


@pytest.mark.asyncio
async def test_batch_preserves_duplicate_url_errors_by_index(monkeypatch):
    urls = ["https://same.test", "https://same.test"]
    processor = BatchProcessor(mode="fast", timeout=5.0)
    call_counter = {"count": 0}

    async def fake_process_url(url):
        call_counter["count"] += 1
        raise RuntimeError(f"err-{call_counter['count']}")

    monkeypatch.setattr(processor, "process_url", fake_process_url)

    result = await processor.process_batch_async(urls)

    assert result.failed == 2
    assert len(result.errors) == 2
    assert result.errors[0].error == "err-1"
    assert result.errors[1].error == "err-2"
    assert result.errors_by_url["https://same.test"] == ["err-1", "err-2"]


def test_batch_result_normalizes_dict_errors_without_losing_context():
    result = BatchResult(
        total=1,
        successful=0,
        failed=1,
        errors=[
            {
                "index": 0,
                "url": "https://example.com",
                "error": "boom",
                "error_type": "RuntimeError",
                "traceback": "Traceback...",
            }
        ],
    )

    assert result.errors[0].error == "boom"
    assert result.errors[0].error_type == "RuntimeError"
    assert result.errors[0].traceback == "Traceback..."


@pytest.mark.asyncio
async def test_batch_concurrency(local_servers):
    """Test concurrent processing"""
    import time

    urls = [local_servers[0]] * 5

    processor = BatchProcessor(mode="fast", max_concurrent=3, timeout=10.0)

    start = time.time()
    result = await processor.process_batch_async(urls)
    elapsed = time.time() - start

    # With concurrency, should be faster than sequential
    assert result.successful == 5
    # Should complete in reasonable time (concurrent)
    assert elapsed < 15  # Much faster than 5 * timeout


def test_batch_progress_callback(local_servers):
    """Test progress callback"""
    progress_calls = []

    def on_progress(current, total, url):
        progress_calls.append((current, total, url))

    urls = local_servers

    processor = BatchProcessor(mode="fast", timeout=10.0, on_progress=on_progress)

    result = processor.process_batch(urls)

    assert len(progress_calls) == 2
    assert result.successful == 2


@pytest.mark.asyncio
async def test_custom_batch_progress_callback_reports_completed_items():
    progress_calls = []

    def on_progress(current, total, url):
        progress_calls.append((current, total, url, time.perf_counter()))

    class SlowBatchProcessor(BatchProcessor):
        async def process_url(self, url: str):
            await asyncio.sleep(0.1 if url.endswith("first") else 0.2)
            return SafeDocument(
                markdown=url,
                metadata={"url": url},
                token_estimate=1,
                injection_score=0.0,
                content_hash=f"hash-{url}",
            )

    started_at = time.perf_counter()
    processor = SlowBatchProcessor(mode="fast", timeout=5.0, on_progress=on_progress)
    result = await processor.process_batch_async(
        ["https://example.com/first", "https://example.com/second"]
    )

    assert result.successful == 2
    assert [call[0] for call in progress_calls] == [1, 2]
    assert progress_calls[0][3] - started_at >= 0.08
    assert progress_calls[1][3] - started_at >= 0.18


@pytest.mark.asyncio
async def test_custom_batch_progress_callback_errors_do_not_fail_batch(caplog):
    progress_calls = []

    def on_progress(current, total, url):
        progress_calls.append((current, total, url))
        raise RuntimeError("progress callback failed")

    class SimpleBatchProcessor(BatchProcessor):
        async def process_url(self, url: str):
            return SafeDocument(
                markdown=url,
                metadata={"url": url},
                token_estimate=1,
                injection_score=0.0,
                content_hash=f"hash-{url}",
            )

    processor = SimpleBatchProcessor(mode="fast", timeout=5.0, on_progress=on_progress)
    result = await processor.process_batch_async(["https://example.com/one"])

    assert result.successful == 1
    assert result.failed == 0
    assert result.errors == []
    assert result.documents[0] is not None
    assert progress_calls == [(1, 1, "https://example.com/one")]
    assert "Batch progress callback failed" in caplog.text


@pytest.mark.asyncio
async def test_default_batch_use_case_progress_reports_completed_items(monkeypatch):
    progress_calls = []

    def on_progress(current, total, url):
        progress_calls.append((current, total, url, time.perf_counter()))

    class SlowIngestUseCase:
        orchestrator = IngestOrchestrator()

        def execute(self, url: str, config):
            time.sleep(0.1 if url.endswith("first") else 0.2)
            return SafeDocument(
                markdown=url,
                metadata={"url": url},
                token_estimate=1,
                injection_score=0.0,
                content_hash=f"hash-{url}",
            )

        def uses_default_runtime_dependencies(self):
            return False

    started_at = time.perf_counter()
    use_case = BatchIngestUseCase(ingest_use_case=SlowIngestUseCase())
    result = await use_case.execute(
        ["https://example.com/first", "https://example.com/second"],
        config_builder=lambda: IngestConfig(mode="fast"),
        max_concurrent=2,
        on_progress=on_progress,
    )

    assert result.successful == 2
    assert [call[0] for call in progress_calls] == [1, 2]
    assert progress_calls[0][3] - started_at >= 0.08
    assert progress_calls[1][3] - started_at >= 0.18


@pytest.mark.asyncio
async def test_default_batch_progress_callback_errors_do_not_fail_batch(caplog):
    progress_calls = []

    def on_progress(current, total, url):
        progress_calls.append((current, total, url))
        raise RuntimeError("progress callback failed")

    class SimpleIngestUseCase:
        orchestrator = IngestOrchestrator()

        def execute(self, url: str, config):
            return SafeDocument(
                markdown=url,
                metadata={"url": url},
                token_estimate=1,
                injection_score=0.0,
                content_hash=f"hash-{url}",
            )

        def uses_default_runtime_dependencies(self):
            return False

    use_case = BatchIngestUseCase(ingest_use_case=SimpleIngestUseCase())
    result = await use_case.execute(
        ["https://example.com/one"],
        config_builder=lambda: IngestConfig(mode="fast"),
        max_concurrent=1,
        on_progress=on_progress,
    )

    assert result.successful == 1
    assert result.failed == 0
    assert result.errors == []
    assert result.documents[0] is not None
    assert progress_calls == [(1, 1, "https://example.com/one")]
    assert "Batch progress callback failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_follower_failure_reports_progress_for_duplicate_url():
    progress_calls = []

    def on_progress(current, total, url):
        progress_calls.append((current, total, url))

    class FailingIngestUseCase:
        orchestrator = IngestOrchestrator()

        def execute(self, url: str, config):
            time.sleep(0.1)
            raise RuntimeError("leader failed")

        def uses_default_runtime_dependencies(self):
            return False

    use_case = BatchIngestUseCase(ingest_use_case=FailingIngestUseCase())
    result = await use_case.execute(
        ["https://example.com/duplicate", "https://example.com/duplicate"],
        config_builder=lambda: IngestConfig(mode="fast"),
        max_concurrent=2,
        on_progress=on_progress,
    )

    assert result.successful == 0
    assert result.failed == 2
    assert [call[0] for call in progress_calls] == [1, 2]
    assert [call[2] for call in progress_calls] == [
        "https://example.com/duplicate",
        "https://example.com/duplicate",
    ]


@pytest.mark.asyncio
async def test_batch_leader_failure_without_followers_does_not_log_unretrieved_future():
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    contexts = []

    def capture_context(_loop, context):
        contexts.append(context)

    class FailingIngestUseCase:
        orchestrator = IngestOrchestrator()

        def execute(self, url: str, config):
            raise RuntimeError("leader failed")

        def uses_default_runtime_dependencies(self):
            return False

    loop.set_exception_handler(capture_context)
    try:
        use_case = BatchIngestUseCase(ingest_use_case=FailingIngestUseCase())
        result = await use_case.execute(
            ["https://example.com/leader"],
            config_builder=lambda: IngestConfig(mode="fast"),
            max_concurrent=1,
        )
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert result.successful == 0
    assert result.failed == 1
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "RuntimeError"
    assert [
        context
        for context in contexts
        if context.get("message") == "Future exception was never retrieved"
    ] == []


def test_batch_result_stats():
    """Test BatchResult statistics"""
    result = BatchResult(total=10, successful=7, failed=3)

    assert result.success_rate == 70.0

    # Empty result
    empty = BatchResult(total=0, successful=0, failed=0)
    assert empty.success_rate == 0.0


@pytest.mark.asyncio
async def test_batch_preserves_url_order_under_concurrency(monkeypatch):
    """Ensure documents keep input URL order even when tasks finish out of order."""
    urls = [
        "https://example.com/slow",
        "https://example.com/fast",
    ]
    processor = BatchProcessor(mode="fast", max_concurrent=2, timeout=5.0)

    async def fake_process_url(url):
        import asyncio

        delay = 0.05 if "slow" in url else 0.001
        await asyncio.sleep(delay)
        return SafeDocument(
            markdown=url,
            metadata={"url": url},
            token_estimate=1,
            injection_score=0.0,
            content_hash="sha256:x",
        )

    monkeypatch.setattr(processor, "process_url", fake_process_url)

    result = await processor.process_batch_async(urls)
    assert result.documents[0] is not None
    assert result.documents[1] is not None
    assert result.documents[0].markdown == "https://example.com/slow"
    assert result.documents[1].markdown == "https://example.com/fast"


@pytest.mark.asyncio
async def test_public_ingest_async(local_servers):
    """The public async API should mirror ingest() for a single URL."""
    doc = await ingest_async(local_servers[0], mode="fast", timeout=10.0)

    assert doc.markdown
    assert doc.metadata["mode"] == "fast"


@pytest.mark.asyncio
async def test_public_ingest_many_async(local_servers):
    """The public batch API should process multiple URLs concurrently."""
    result = await ingest_many_async(local_servers, mode="fast", timeout=10.0, max_concurrent=2)

    assert result.total == 2
    assert result.successful == 2
    assert result.failed == 0
    assert [doc.metadata["url"] for doc in result.documents if doc is not None] == local_servers


def test_public_ingest_many_sync(local_servers):
    """The sync wrapper should expose batch ingestion for normal library consumers."""
    result = ingest_many(local_servers, mode="fast", timeout=10.0, max_concurrent=2)

    assert result.successful == 2
    assert result.failed == 0
    assert len(result.documents) == 2


def test_public_ingest_many_sync_rejects_active_event_loop():
    """The sync wrapper should fail fast when used from async code."""

    async def run_inside_loop():
        with pytest.raises(RuntimeError, match="ingest_many_async"):
            ingest_many(["https://example.com"], mode="fast")

    asyncio.run(run_inside_loop())


@pytest.mark.asyncio
async def test_public_ingest_many_async_handles_failures(local_servers):
    """The public batch API should keep successes and report failures."""
    urls = [local_servers[0], "http://127.0.0.1:1"]

    result = await ingest_many_async(urls, mode="fast", timeout=2.0, max_concurrent=2)

    assert result.total == 2
    assert result.successful == 1
    assert result.failed == 1
    assert result.documents[0] is not None
    assert result.documents[1] is None
    assert any(item.url == "http://127.0.0.1:1" for item in result.errors)


def test_public_ingest_many_sync_uses_file_config_batch_settings(monkeypatch):
    captured: dict[str, object] = {}

    def fake_ingest_many_sync_impl(
        urls,
        *,
        playwright_available,
        max_concurrent,
        on_progress,
        **runtime_kwargs,
    ):
        captured["urls"] = list(urls)
        captured["max_concurrent"] = max_concurrent
        captured["timeout"] = runtime_kwargs.get("timeout")
        return "ok"

    monkeypatch.setattr(public_api, "ingest_many_sync_impl", fake_ingest_many_sync_impl)

    result = ingest_many(
        ["https://example.com"],
        config=Config(batch_max_concurrent=17, batch_timeout=42.0),
    )

    assert result == "ok"
    assert captured == {
        "urls": ["https://example.com"],
        "max_concurrent": 17,
        "timeout": 42.0,
    }


@pytest.mark.asyncio
async def test_public_ingest_many_async_uses_file_config_batch_settings(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_ingest_many_async_impl(
        urls,
        *,
        playwright_available,
        max_concurrent,
        on_progress,
        **runtime_kwargs,
    ):
        captured["urls"] = list(urls)
        captured["max_concurrent"] = max_concurrent
        captured["timeout"] = runtime_kwargs.get("timeout")
        return "ok"

    monkeypatch.setattr(public_api, "ingest_many_async_impl", fake_ingest_many_async_impl)

    result = await ingest_many_async(
        ["https://example.com"],
        config=Config(batch_max_concurrent=17, batch_timeout=42.0),
    )

    assert result == "ok"
    assert captured == {
        "urls": ["https://example.com"],
        "max_concurrent": 17,
        "timeout": 42.0,
    }


def test_public_ingest_many_sync_explicit_args_override_file_config_batch_settings(monkeypatch):
    captured: dict[str, object] = {}

    def fake_ingest_many_sync_impl(
        urls,
        *,
        playwright_available,
        max_concurrent,
        on_progress,
        **runtime_kwargs,
    ):
        captured["max_concurrent"] = max_concurrent
        captured["timeout"] = runtime_kwargs.get("timeout")
        return "ok"

    monkeypatch.setattr(public_api, "ingest_many_sync_impl", fake_ingest_many_sync_impl)

    result = ingest_many(
        ["https://example.com"],
        config=Config(batch_max_concurrent=17, batch_timeout=42.0),
        timeout=9.0,
        max_concurrent=3,
    )

    assert result == "ok"
    assert captured == {"max_concurrent": 3, "timeout": 9.0}


@pytest.mark.asyncio
async def test_public_ingest_many_async_explicit_args_override_file_config_batch_settings(
    monkeypatch,
):
    captured: dict[str, object] = {}

    async def fake_ingest_many_async_impl(
        urls,
        *,
        playwright_available,
        max_concurrent,
        on_progress,
        **runtime_kwargs,
    ):
        captured["max_concurrent"] = max_concurrent
        captured["timeout"] = runtime_kwargs.get("timeout")
        return "ok"

    monkeypatch.setattr(public_api, "ingest_many_async_impl", fake_ingest_many_async_impl)

    result = await ingest_many_async(
        ["https://example.com"],
        config=Config(batch_max_concurrent=17, batch_timeout=42.0),
        timeout=9.0,
        max_concurrent=3,
    )

    assert result == "ok"
    assert captured == {"max_concurrent": 3, "timeout": 9.0}


def test_resolve_batch_api_options_uses_batch_settings_from_converted_runtime_config():
    runtime_config = Config(
        batch_max_concurrent=9,
        batch_timeout=12.0,
    ).to_ingest_config()

    resolved_timeout, resolved_max_concurrent = resolve_batch_api_options(
        runtime_config,
        timeout=UNSET,
        max_concurrent=UNSET,
    )

    assert resolved_timeout == 12.0
    assert resolved_max_concurrent == 9


@pytest.mark.parametrize("bad_max_concurrent", [True, 1.5, "2"])
def test_resolve_batch_api_options_rejects_non_integer_max_concurrent(bad_max_concurrent):
    with pytest.raises(ValueError, match="max_concurrent must be an int"):
        resolve_batch_api_options(
            None,
            timeout=UNSET,
            max_concurrent=bad_max_concurrent,
        )


def test_resolve_batch_api_options_rejects_zero_max_concurrent():
    with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
        resolve_batch_api_options(None, timeout=UNSET, max_concurrent=0)


# --- Bug fix tests ---


def test_make_picklable_preserves_keys_with_unpicklable_values():
    """_make_picklable must convert unpicklable leaves to str, not drop them."""
    from markdown_ingress.application.exceptions import _make_picklable

    class _Unpicklable:
        def __reduce__(self):
            raise TypeError("cannot pickle")

        def __str__(self):
            return "unpicklable-sentinel"

    result = _make_picklable({"key": _Unpicklable(), "safe": 42})
    assert "key" in result, "unpicklable key must not be dropped"
    assert result["key"] == "unpicklable-sentinel"
    assert result["safe"] == 42


def test_make_picklable_preserves_list_items_with_unpicklable_values():
    from markdown_ingress.application.exceptions import _make_picklable

    class _Unpicklable:
        def __reduce__(self):
            raise TypeError("cannot pickle")

        def __str__(self):
            return "item-sentinel"

    result = _make_picklable([_Unpicklable(), "ok"])
    assert len(result) == 2, "unpicklable list items must not be dropped"
    assert result[0] == "item-sentinel"
    assert result[1] == "ok"


def test_copy_batch_exception_preserves_original_as_cause_after_deepcopy():
    from markdown_ingress.application.exceptions import _copy_batch_exception

    original = ValueError("leader failed")

    copied = _copy_batch_exception(original)

    assert copied is not original
    assert type(copied) is ValueError
    assert copied.__cause__ is original


def test_copy_batch_exception_sanitizes_args_and_attrs_for_pickle():
    from markdown_ingress.application.exceptions import _copy_batch_exception

    original = _CopyBatchExceptionError(lambda: None)
    original.payload = lambda: None

    copied = _copy_batch_exception(original)

    assert type(copied) is _CopyBatchExceptionError
    assert isinstance(copied.args[0], str)
    assert isinstance(copied.payload, str)
    pickle.dumps(copied)


def test_inflight_followers_decremented_after_await():
    """await_result must decrement followers on successful completion."""
    import threading
    import time

    from markdown_ingress.core.inflight import InFlightRegistry
    from markdown_ingress.models import SafeDocument

    registry = InFlightRegistry()
    request_key = "test-followers-decrement"

    # Leader acquires — returns None (new entry created internally)
    leader_result = registry.acquire(request_key)
    assert leader_result is None

    # Follower acquires the same key — returns the shared entry
    follower_entry = registry.acquire(request_key)
    assert follower_entry is not None
    assert follower_entry.followers == 1

    doc = SafeDocument(
        markdown="# hi",
        metadata={"url": "http://example.com"},
        token_estimate=5,
        content_hash="abc",
        injection_score=0.0,
    )

    # Release from a background thread so await_result can unblock
    def release():
        time.sleep(0.05)
        registry.release(request_key, document=doc)

    t = threading.Thread(target=release, daemon=True)
    t.start()

    registry.await_result(follower_entry, request_key)
    t.join(timeout=2)

    assert follower_entry.followers == 0


def test_inflight_error_preserves_original_as_cause_after_deepcopy():
    from markdown_ingress.core.inflight import InFlightRegistry

    registry = InFlightRegistry()
    request_key = "test-error-cause"
    assert registry.acquire(request_key) is None
    follower_entry = registry.acquire(request_key)
    assert follower_entry is not None

    original = ValueError("leader failed")
    assert registry.release(request_key, error=original) == 1

    with pytest.raises(ValueError) as exc_info:
        registry.await_result(follower_entry, request_key)

    assert exc_info.value is not original
    assert exc_info.value.__cause__ is original


def test_inflight_late_done_entry_counts_follower_before_await():
    """A late duplicate that sees a done entry must not drive followers negative."""
    from markdown_ingress.core.inflight import InFlightEntry, InFlightRegistry
    from markdown_ingress.models import SafeDocument

    registry = InFlightRegistry()
    request_key = "test-late-done-entry"
    doc = SafeDocument(
        markdown="# hi",
        metadata={"url": "http://example.com"},
        token_estimate=5,
        content_hash="abc",
        injection_score=0.0,
    )
    entry = InFlightEntry(request_key=request_key)
    entry.done = True
    entry.document = doc
    registry._requests[request_key] = entry

    late_entry = registry.acquire(request_key)

    assert late_entry is entry
    assert entry.followers == 1
    registry.await_result(late_entry, request_key)
    assert entry.followers == 0


def test_decode_content_invalid_utf8_uses_replacement_char():
    """_decode_content must use U+FFFD for invalid bytes, not corrupt via latin-1."""
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    # 0xFF is invalid in UTF-8 — latin-1 would produce 'ÿ', replacement char produces '\\ufffd'
    invalid_utf8 = b"hello \xff world"
    result = Fetcher._decode_content(invalid_utf8, None)
    assert "\ufffd" in result, "replacement character expected for invalid UTF-8 byte"
    assert "ÿ" not in result, "latin-1 mojibake must not appear"
