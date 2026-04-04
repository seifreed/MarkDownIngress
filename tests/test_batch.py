"""Tests for batch processing"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markdown_ingress.application.batch import BatchProcessor
from markdown_ingress.application.use_cases import BatchIngestUseCase, IngestUseCase
from markdown_ingress import ingest_async, ingest_many, ingest_many_async
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.inflight import InFlightRegistry
from markdown_ingress.core.orchestrator import IngestOrchestrator
from markdown_ingress.models import FetchResult, SafeDocument
from markdown_ingress.shared_results import BatchResult


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
        raise AssertionError("batch should not use subprocess isolation without importable __main__")

    monkeypatch.setattr(BatchIngestUseCase, "_main_module_file", staticmethod(lambda: None))
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
    use_case = IngestUseCase(
        orchestrator=IngestOrchestrator(inflight_registry=InFlightRegistry())
    )

    assert use_case.uses_default_runtime_dependencies() is False


def test_batch_selects_local_strategy_for_injected_inflight_registry():
    batch_use_case = BatchIngestUseCase(
        ingest_use_case=IngestUseCase(
            orchestrator=IngestOrchestrator(inflight_registry=InFlightRegistry())
        )
    )

    strategy, reason = batch_use_case._select_execution_strategy()

    assert strategy == "local"
    assert reason == "custom runtime dependencies"


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
        return type(
            "Doc",
            (),
            {"markdown": url, "token_estimate": 1, "injection_score": 0.0, "content_hash": "sha256:x", "metadata": {}},
        )()

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
