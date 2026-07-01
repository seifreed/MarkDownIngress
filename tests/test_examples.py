"""Import checks for example scripts that are safe at module import time."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_example(example_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"markdown_ingress_example_{example_path.stem}", example_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "example_path",
    [
        Path("examples/advanced_stealth_example.py"),
        Path("examples/demo_resource_blocking.py"),
        Path("examples/extreme_mode_example.py"),
        Path("examples/library_batch_async.py"),
        Path("examples/library_usage.py"),
        Path("examples/retry_examples.py"),
    ],
)
def test_import_safe_examples_load(example_path: Path) -> None:
    original_path = list(sys.path)
    try:
        _load_example(example_path)
        assert sys.path == original_path
    finally:
        sys.path[:] = original_path


def test_library_usage_example_uses_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_example(Path("examples/library_usage.py"))
    seen: list[str] = []

    def fake_ingest(url: str, *, config):
        seen.append(url)
        return SimpleNamespace(metadata={}, token_estimate=1, injection_score=0.0)

    monkeypatch.setenv("MDI_EXAMPLE_URL", "https://docs.example.test/page")
    monkeypatch.setattr(module, "ingest", fake_ingest)

    module.main()

    assert seen == ["https://docs.example.test/page"]


def test_library_batch_example_uses_env_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_example(Path("examples/library_batch_async.py"))
    seen: list[list[str]] = []

    async def fake_ingest_many_async(urls, *, config, max_concurrent):
        seen.append(list(urls))
        docs = [
            SimpleNamespace(token_estimate=1),
            SimpleNamespace(token_estimate=2),
        ]
        return SimpleNamespace(successful=2, failed=0, documents=docs, error_items=[])

    monkeypatch.setenv("MDI_EXAMPLE_URLS", "https://one.example.test,https://two.example.test")
    monkeypatch.setattr(module, "ingest_many_async", fake_ingest_many_async)
    monkeypatch.setattr(
        module,
        "get_ingest_stats",
        lambda: {
            "requests_total": 2,
            "cache_hits": 0,
            "inflight_followers": 0,
            "mode_results": {"fast": {"success": 2}},
            "mode_timings_ms": {"fast": {"avg": 1.0}},
        },
    )

    asyncio.run(module.main())

    assert seen == [["https://one.example.test", "https://two.example.test"]]


def test_retry_example_uses_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_example(Path("examples/retry_examples.py"))
    seen: list[str] = []

    def fake_retry_ingest(url: str):
        seen.append(url)
        return SimpleNamespace(
            metadata={"retry_attempts": 1, "final_timeout": 1.0, "retry_enabled": False},
            token_estimate=1,
        )

    monkeypatch.setenv("MDI_EXAMPLE_URL", "https://retry.example.test")
    monkeypatch.setattr(module, "retry_ingest", fake_retry_ingest)

    module.main()

    assert seen == ["https://retry.example.test"]


def test_resource_blocking_example_uses_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_example(Path("examples/demo_resource_blocking.py"))
    seen: list[str] = []

    class FakeRenderer:
        def __init__(self, **kwargs):
            self.block_resources = kwargs.get("block_resources", False)

        async def render(self, url: str):
            seen.append(url)
            return SimpleNamespace(
                timing_ms=1.0,
                html="<html></html>",
                status_code=200,
                metadata=(
                    {
                        "total_requests": 1,
                        "blocked_requests": 0,
                        "block_rate_pct": 0.0,
                        "blocked_by_type": {},
                    }
                    if self.block_resources
                    else {}
                ),
            )

    monkeypatch.setenv("MDI_EXAMPLE_URL", "https://render.example.test")
    monkeypatch.setattr(module, "Renderer", FakeRenderer)

    asyncio.run(module.demo_resource_blocking())

    assert seen == ["https://render.example.test", "https://render.example.test"]


def test_advanced_stealth_example_uses_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_example(Path("examples/advanced_stealth_example.py"))
    seen: list[str] = []

    class FakeAdvancedStealthRenderer:
        def __init__(self, **kwargs):
            pass

        async def render(self, url: str):
            seen.append(url)
            return SimpleNamespace(status_code=200, timing_ms=1.0)

    monkeypatch.setenv("MDI_EXAMPLE_STEALTH_URL", "https://stealth.example.test")
    monkeypatch.setattr(module, "AdvancedStealthRenderer", FakeAdvancedStealthRenderer)

    asyncio.run(module.example_custom_config())

    assert seen == ["https://stealth.example.test"]
