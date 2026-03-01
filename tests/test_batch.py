"""Tests for batch processing"""

import pytest

from markdown_ingress.core.batch import BatchProcessor, BatchResult


@pytest.mark.asyncio
async def test_batch_processor_basic():
    """Test basic batch processing"""
    urls = [
        "http://example.com",
        "http://httpbin.org/html",
    ]

    processor = BatchProcessor(mode="fast", timeout=15.0, max_concurrent=2)
    result = await processor.process_batch_async(urls)

    assert result.total == 2
    assert result.successful == 2
    assert result.failed == 0
    assert len(result.documents) == 2
    assert result.success_rate == 100.0


def test_batch_processor_sync():
    """Test synchronous batch processing"""
    urls = ["http://example.com"]

    processor = BatchProcessor(mode="fast", timeout=10.0)
    result = processor.process_batch(urls)

    assert result.successful == 1
    assert len(result.documents) == 1


@pytest.mark.asyncio
async def test_batch_with_errors():
    """Test batch processing with some failures"""
    urls = [
        "http://example.com",
        "http://invalid-url-that-does-not-exist-12345.com",
    ]

    processor = BatchProcessor(mode="fast", timeout=5.0)
    result = await processor.process_batch_async(urls)

    assert result.total == 2
    assert result.successful == 1
    assert result.failed == 1
    assert len(result.errors) == 1


@pytest.mark.asyncio
async def test_batch_concurrency():
    """Test concurrent processing"""
    import time

    urls = ["http://example.com"] * 5

    processor = BatchProcessor(mode="fast", max_concurrent=3, timeout=10.0)

    start = time.time()
    result = await processor.process_batch_async(urls)
    elapsed = time.time() - start

    # With concurrency, should be faster than sequential
    assert result.successful == 5
    # Should complete in reasonable time (concurrent)
    assert elapsed < 15  # Much faster than 5 * timeout


def test_batch_progress_callback():
    """Test progress callback"""
    progress_calls = []

    def on_progress(current, total, url):
        progress_calls.append((current, total, url))

    urls = ["http://example.com", "http://httpbin.org/html"]

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
