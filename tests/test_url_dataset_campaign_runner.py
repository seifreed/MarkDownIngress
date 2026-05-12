"""Unit tests for the URL campaign runner helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from markdown_ingress.adapters.fetching.httpx_fetcher import UnsupportedContentTypeError
from markdown_ingress.config_models import IngestConfig
from tests.url_dataset_campaign import (
    CampaignScenario,
    _load_availability_pool,
    availability_pool_path,
    classify_error,
    collect_available_urls,
    diversify_urls_by_host,
    run_campaign,
)


def test_classify_error_maps_playwright_download_to_non_html():
    exc = RuntimeError("Page.goto: Download is starting")
    assert classify_error(exc) == "dataset_non_html"


def test_classify_error_maps_playwright_name_resolution_to_invalid_host():
    exc = RuntimeError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://example.invalid")
    assert classify_error(exc) == "dataset_invalid_host"


def test_classify_error_maps_playwright_connection_timeout_to_timeout():
    exc = RuntimeError("Page.goto: net::ERR_CONNECTION_TIMED_OUT at https://slow.example")
    assert classify_error(exc) == "timeout"


def test_classify_error_maps_playwright_internet_disconnected():
    exc = RuntimeError("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://offline.example")
    assert classify_error(exc) == "network_disconnected"


def test_classify_error_maps_playwright_network_io_suspended():
    exc = RuntimeError("Page.goto: net::ERR_NETWORK_IO_SUSPENDED at https://flaky.example")
    assert classify_error(exc) == "network_io_suspended"


def test_classify_error_maps_playwright_err_failed_to_navigation_failed():
    exc = RuntimeError("Page.goto: net::ERR_FAILED at https://broken.example/")
    assert classify_error(exc) == "navigation_failed"


def test_classify_error_preserves_non_html_exception_classification():
    exc = UnsupportedContentTypeError(
        "Unsupported content type for HTML ingestion: application/pdf"
    )
    assert classify_error(exc) == "dataset_non_html"


def test_classify_error_maps_http_status_variants():
    assert classify_error(RuntimeError("Client error '401 Unauthorized'")) == "unauthorized"
    assert classify_error(RuntimeError("Client error '410 Gone'")) == "gone"
    assert (
        classify_error(RuntimeError("Client error '451 Unavailable For Legal Reasons'"))
        == "legal_block"
    )
    assert classify_error(RuntimeError("Server error '503 Service Unavailable'")) == "server_error"


def test_diversify_urls_by_host_limits_single_host_dominance():
    urls = [
        "https://a.example/1",
        "https://a.example/2",
        "https://a.example/3",
        "https://a.example/4",
        "https://b.example/1",
        "https://c.example/1",
        "https://d.example/1",
    ]
    selected = diversify_urls_by_host(urls, 5, soft_cap=1)
    assert len(selected) == 5
    assert sum(1 for url in selected if "a.example" in url) == 2


def test_collect_available_urls_replaces_unavailable_entries(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tests.url_dataset_campaign.output_dir", lambda: tmp_path)
    urls = [
        "https://bad.example/403",
        "https://bad.example/404",
        "https://good.example/1",
        "https://slow.example/timeout",
        "https://good.example/2",
        "https://good.example/3",
    ]

    monkeypatch.setattr("tests.url_dataset_campaign.load_unique_urls", lambda limit: urls)

    def fake_fetch_sync(self, url: str):
        if "403" in url:
            raise RuntimeError("Client error '403 Forbidden'")
        if "404" in url:
            raise RuntimeError("Client error '404 Not Found'")
        if "timeout" in url:
            raise RuntimeError("timed out")
        return object()

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.Fetcher.fetch_sync", fake_fetch_sync
    )

    selected, dropped, errors, checked = asyncio.run(
        collect_available_urls(total_limit=3, concurrency=4, timeout=1.0)
    )

    assert selected == [
        "https://good.example/1",
        "https://good.example/2",
        "https://good.example/3",
    ]
    assert dropped == {}
    assert errors["forbidden"] == 1
    assert errors["not_found"] == 1
    assert errors["timeout"] == 1
    assert checked >= 5


def test_collect_available_urls_reuses_persisted_pool(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tests.url_dataset_campaign.output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "tests.url_dataset_campaign.load_unique_urls",
        lambda limit: [
            "https://good.example/1",
            "https://bad.example/403",
            "https://good.example/2",
        ],
    )

    calls = {"count": 0}

    def fake_fetch_sync(self, url: str):
        calls["count"] += 1
        if "403" in url:
            raise RuntimeError("Client error '403 Forbidden'")
        return object()

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.Fetcher.fetch_sync", fake_fetch_sync
    )

    selected_first, _, _, checked_first = asyncio.run(
        collect_available_urls(total_limit=2, concurrency=4, timeout=1.0)
    )
    selected_second, _, _, checked_second = asyncio.run(
        collect_available_urls(total_limit=2, concurrency=4, timeout=1.0)
    )

    assert selected_first == ["https://good.example/1", "https://good.example/2"]
    assert selected_second == selected_first
    assert checked_first == 3
    assert checked_second == 3
    assert calls["count"] == 3
    assert availability_pool_path().exists()


def test_load_availability_pool_uses_latest_status_per_url(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tests.url_dataset_campaign.output_dir", lambda: tmp_path)
    pool_path = availability_pool_path()
    pool_path.write_text(
        "\n".join(
            [
                json.dumps({"url": "https://example.com/a", "status": "timeout"}),
                json.dumps({"url": "https://example.com/a", "status": "available"}),
                json.dumps({"url": "https://example.com/b", "status": "forbidden"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    statuses, counts = _load_availability_pool()

    assert statuses == {
        "https://example.com/a": "available",
        "https://example.com/b": "forbidden",
    }
    assert counts == {"available": 1, "forbidden": 1}


def test_run_campaign_writes_preselection_progress(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tests.url_dataset_campaign.output_dir", lambda: tmp_path)
    dataset_file = tmp_path / "out.txt"
    dataset_file.write_text("\n".join(f"https://good.example/{idx}" for idx in range(4)))
    monkeypatch.setattr("tests.url_dataset_campaign.download_dataset", lambda: dataset_file)

    async def fake_collect_available_urls(
        *, total_limit, concurrency, progress_callback=None, timeout=5.0
    ):
        urls = [f"https://good.example/{idx}" for idx in range(total_limit)]
        errors = {"available": total_limit}
        if progress_callback is not None:
            progress_callback(
                availability_checked=total_limit,
                availability_error_types=errors,
                dropped_url_types={},
                selected_count=total_limit,
            )
        return urls, {}, errors, total_limit

    monkeypatch.setattr(
        "tests.url_dataset_campaign.collect_available_urls", fake_collect_available_urls
    )

    async def fake_ingest_async(url: str, config):
        from types import SimpleNamespace

        return SimpleNamespace(
            flags=[],
            removed_elements={},
            injection_score=0.0,
            metadata={
                "mode": "fast",
                "status_code": 200,
                "fetch_time_ms": 1.0,
                "policy_action": "allow",
            },
            token_estimate=1,
        )

    monkeypatch.setattr("tests.url_dataset_campaign.ingest_async", fake_ingest_async)

    summary = run_campaign(
        total_limit=4,
        scenarios=[
            CampaignScenario(
                name="fast_default",
                description="x",
                weight=1,
                config=IngestConfig(mode="fast", timeout=1.0),
                max_concurrency=1,
            )
        ],
        concurrency=2,
        batch_size=2,
    )

    progress = json.loads((Path(summary["run_dir"]) / "progress.json").read_text(encoding="utf-8"))
    assert progress["current_scenario"] == "fast_default"
    assert progress["availability_checked"] == 4
