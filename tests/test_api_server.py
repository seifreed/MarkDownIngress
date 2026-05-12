"""
Tests for FastAPI server endpoints
"""

import asyncio
import importlib
import logging
import socket
import sqlite3
import sys
import threading
import time
import types
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import markdown_ingress.api_server as api_server
from markdown_ingress.adapters.fetching.httpx_fetcher import (
    DomainCircuitOpenError,
    UnsupportedContentTypeError,
)
from markdown_ingress.api_server import (
    LEGACY_UNKNOWN_TTL_SECONDS,
    _get_job_queue,
    _get_job_record,
    _init_job_queue,
    _snapshot_job_subsystem,
    _start_job_queue_repair_loop,
    app,
)
from markdown_ingress.api_server_handlers import handle_batch_submit, handle_sync_batch
from markdown_ingress.api_server_models import (
    BatchIngestRequest,
    DomainPolicyModel,
    HTMLCompareRequest,
    IngestRequest,
    RetryIngestRequest,
)
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.shared_results import BatchErrorItem

client = TestClient(app)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://10.0.0.1",
        "http://[::1]/",
        "http://localhost./",
        "http://metadata.google.internal./",
        "http://metadata.azure.net/",
        "http://metadata.azure.net./",
        "http://metadata.oracle.internal/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:10.0.0.1]/",
    ],
)
def test_api_server_models_block_private_and_loopback_urls(monkeypatch, url: str):
    monkeypatch.setenv("MDI_ALLOW_LOCAL_URLS", "false")
    with pytest.raises(ValueError, match="SSRF protection"):
        IngestRequest(url=cast(Any, url))

    with pytest.raises(ValueError, match="SSRF protection"):
        RetryIngestRequest(url=cast(Any, url))

    with pytest.raises(ValueError, match="SSRF protection"):
        BatchIngestRequest(urls=[cast(Any, url)])


def test_api_server_models_block_hostnames_resolving_private(monkeypatch):
    monkeypatch.setenv("MDI_ALLOW_LOCAL_URLS", "false")

    def fake_getaddrinfo(host, *_args, **_kwargs):
        assert host == "public.example.test"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("markdown_ingress.core.ssrf.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="SSRF protection"):
        IngestRequest(url="http://public.example.test/private")

    with pytest.raises(ValueError, match="SSRF protection"):
        RetryIngestRequest(url="http://public.example.test/private")

    # BatchIngestRequest defers DNS-based SSRF validation to the async endpoint
    # handler to avoid blocking the event loop with synchronous DNS lookups.
    batch = BatchIngestRequest(urls=["http://public.example.test/private"])
    assert str(batch.urls[0]) == "http://public.example.test/private"


def test_batch_request_blocks_metadata_azure_webhook_url():
    with pytest.raises(ValueError, match="SSRF protection"):
        BatchIngestRequest(
            urls=["https://example.com"],
            webhook_url="https://metadata.azure.net/hook",
        )


def test_batch_request_blocks_metadata_oracle_webhook_url():
    with pytest.raises(ValueError, match="SSRF protection"):
        BatchIngestRequest(
            urls=["https://example.com"],
            webhook_url="https://metadata.oracle.internal/hook",
        )


def test_batch_request_blocks_local_webhook_url_by_default():
    with pytest.raises(ValueError, match="SSRF protection"):
        BatchIngestRequest(
            urls=["https://example.com"],
            webhook_url="http://127.0.0.1/hook",
        )


def test_batch_request_allows_local_webhook_url_when_enabled(monkeypatch):
    monkeypatch.setenv("MDI_API_ALLOW_LOCAL_WEBHOOKS", "true")

    request = BatchIngestRequest(
        urls=["https://example.com"],
        webhook_url="http://127.0.0.1/hook",
    )

    assert str(request.webhook_url) == "http://127.0.0.1/hook"


def test_ingest_request_rejects_invalid_output_formats_early():
    with pytest.raises(ValueError, match="output_formats\\[0\\] has invalid value 'bogus'"):
        IngestRequest(url="https://example.com", output_formats=["bogus"])


def test_batch_request_rejects_invalid_output_formats_early():
    with pytest.raises(ValueError, match="output_formats\\[0\\] has invalid value 'bogus'"):
        BatchIngestRequest(urls=["https://example.com"], output_formats=["bogus"])


@pytest.mark.parametrize("raw_value", ["nan", "inf", "-inf"])
def test_read_optional_float_env_rejects_non_finite_values(monkeypatch, raw_value: str):
    from markdown_ingress.api_server_env import _read_optional_float_env

    monkeypatch.setenv("MDI_TEST_FLOAT", raw_value)

    assert _read_optional_float_env("MDI_TEST_FLOAT", minimum=0.0) is None


def test_ingest_request_rejects_invalid_policy_name_early():
    with pytest.raises(ValueError, match="policy_name has invalid value 'bogus'"):
        IngestRequest(url="https://example.com", policy_name="bogus")


def test_batch_request_rejects_invalid_policy_name_early():
    with pytest.raises(ValueError, match="policy_name has invalid value 'bogus'"):
        BatchIngestRequest(urls=["https://example.com"], policy_name="bogus")


def test_domain_policy_model_rejects_invalid_policy_name_early():
    with pytest.raises(ValueError, match="policy_name has invalid value 'bogus'"):
        DomainPolicyModel(domain="example.com", policy_name="bogus")


def test_ingest_request_rejects_invalid_output_profile_early():
    with pytest.raises(ValueError, match="Unknown output profile 'bogus'"):
        IngestRequest(url="https://example.com", output_profile="bogus")


def test_batch_request_rejects_invalid_output_profile_early():
    with pytest.raises(ValueError, match="Unknown output profile 'bogus'"):
        BatchIngestRequest(urls=["https://example.com"], output_profile="bogus")


def test_domain_policy_model_rejects_invalid_output_profile_early():
    with pytest.raises(ValueError, match="Unknown output profile 'bogus'"):
        DomainPolicyModel(domain="example.com", output_profile="bogus")


def test_ingest_request_rejects_client_supplied_allow_local_urls():
    """Security fix (S1): allow_local_urls must be server-side only (env var)."""
    with pytest.raises(ValueError):
        IngestRequest(url="https://example.com", allow_local_urls=True)


def test_batch_request_rejects_client_supplied_allow_local_urls():
    """Security fix (S1): allow_local_urls must be server-side only (env var)."""
    with pytest.raises(ValueError):
        BatchIngestRequest(urls=["https://example.com"], allow_local_urls=True)


def test_retry_request_rejects_client_supplied_allow_local_urls():
    """Security fix (S1): allow_local_urls must be server-side only (env var)."""
    with pytest.raises(ValueError):
        RetryIngestRequest(url="https://example.com", allow_local_urls=True)


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (IngestRequest, {"url": "https://example.com"}),
        (BatchIngestRequest, {"urls": ["https://example.com"]}),
    ],
)
def test_request_models_reject_client_screenshot_paths(factory, kwargs):
    with pytest.raises(ValueError, match="screenshot must be a boolean or null"):
        factory(**kwargs, screenshot="/tmp/mdingress-owned.png")


@pytest.mark.parametrize("screenshot", [True, False, None])
def test_request_models_accept_server_managed_screenshot_values(screenshot: bool | None):
    ingest_request = IngestRequest(url=cast(Any, "https://example.com"), screenshot=screenshot)
    batch_request = BatchIngestRequest(
        urls=[cast(Any, "https://example.com")], screenshot=screenshot
    )

    assert ingest_request.screenshot is screenshot
    assert batch_request.screenshot is screenshot


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: IngestRequest(url=cast(Any, "https://example.com"), strict=cast(Any, "false")),
            "Input should be a valid boolean",
        ),
        (
            lambda: IngestRequest(url=cast(Any, "https://example.com"), timeout=cast(Any, "30")),
            "Input should be a valid number",
        ),
        (
            lambda: IngestRequest(
                url=cast(Any, "https://example.com"), chunk_size=cast(Any, "1000")
            ),
            "Input should be a valid integer",
        ),
        (
            lambda: BatchIngestRequest(
                urls=[cast(Any, "https://example.com")], strict=cast(Any, "false")
            ),
            "Input should be a valid boolean",
        ),
        (
            lambda: BatchIngestRequest(
                urls=[cast(Any, "https://example.com")], max_concurrent=cast(Any, "2")
            ),
            "Input should be a valid integer",
        ),
        (
            lambda: RetryIngestRequest(
                url=cast(Any, "https://example.com"), enable_stealth=cast(Any, "false")
            ),
            "Input should be a valid boolean",
        ),
        (
            lambda: DomainPolicyModel(domain="example.com", strict=cast(Any, "false")),
            "Input should be a valid boolean",
        ),
        (
            lambda: DomainPolicyModel(domain="example.com", timeout=cast(Any, "10")),
            "Input should be a valid number",
        ),
        (
            lambda: HTMLCompareRequest(html=cast(Any, 123)),
            "Input should be a valid string",
        ),
    ],
)
def test_request_models_reject_coerced_scalar_types(
    factory: Callable[[], object],
    message: str,
):
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (IngestRequest, {"url": cast(Any, "http://127.0.0.1:8000")}),
        (BatchIngestRequest, {"urls": [cast(Any, "http://127.0.0.1:8000")]}),
        (RetryIngestRequest, {"url": cast(Any, "http://127.0.0.1:8000")}),
    ],
)
def test_request_models_honor_env_allow_local_urls_fallback(monkeypatch, factory, kwargs):
    """When env var is set, local-URL validation must permit local targets."""
    monkeypatch.setenv("MDI_ALLOW_LOCAL_URLS", "true")

    # Should not raise — env var opts into local URL acceptance.
    factory(**kwargs)


def test_ingest_request_rejects_empty_reports_dir_early():
    with pytest.raises(ValueError, match="reports_dir cannot be empty"):
        IngestRequest(url="https://example.com", reports_dir="   ")


def test_batch_request_rejects_empty_reports_dir_early():
    with pytest.raises(ValueError, match="reports_dir cannot be empty"):
        BatchIngestRequest(urls=["https://example.com"], reports_dir="")


@pytest.mark.parametrize("reports_dir", ["/tmp/markdown-ingress", r"C:\tmp\markdown-ingress"])
def test_requests_reject_absolute_reports_dir_early(reports_dir: str):
    with pytest.raises(ValueError, match="reports_dir must be relative"):
        IngestRequest(url=cast(Any, "https://example.com"), reports_dir=reports_dir)

    with pytest.raises(ValueError, match="reports_dir must be relative"):
        BatchIngestRequest(urls=[cast(Any, "https://example.com")], reports_dir=reports_dir)


def test_ingest_request_rejects_unknown_extra_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        IngestRequest(url="https://example.com", ouput_profile="bogus")


def test_batch_request_rejects_unknown_extra_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        BatchIngestRequest(urls=["https://example.com"], allow_local_url=True)


def test_retry_request_rejects_unknown_extra_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        RetryIngestRequest(url="https://example.com", allow_local_url=True)


def test_domain_policy_model_rejects_unknown_extra_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        DomainPolicyModel(domain="example.com", include_subdomainz=True)


def test_handle_sync_batch_maps_value_error_to_http_400():
    request = BatchIngestRequest(urls=["https://example.com"])

    def bad_ingest_many(**kwargs):
        raise ValueError("bad runtime config")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(handle_sync_batch(request, bad_ingest_many))

    # Security fix (S3): ValueError messages may carry internal IPs/paths,
    # so the handler masks them with a generic detail.
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid request"


def test_handle_batch_submit_maps_webhook_validation_error_to_http_400():
    request = BatchIngestRequest(
        urls=["https://example.com"],
        webhook_url="https://hooks.example.com/notify",
    )

    class BadQueue:
        def submit(self, *args, **kwargs):
            raise ValueError("webhook_url blocked: hostname resolves to blocked IP")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(handle_batch_submit(request, lambda **kwargs: None, BadQueue(), 3600))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid request"
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_handle_sync_batch_maps_policy_block_to_http_403():
    request = BatchIngestRequest(urls=["https://example.com"])
    blocked_doc = create_mock_document()
    blocked_doc.metadata["policy_action"] = "block"
    blocked_doc.flags = blocked_doc.flags + ["policy_block"]

    def blocked_ingest_many(**kwargs):
        raise PolicyBlockedError("Blocked by policy", document=blocked_doc)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(handle_sync_batch(request, blocked_ingest_many))

    # Security fix (S12): 403 must not leak internal flags or policy_action.
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["type"] == "policy_blocked"
    assert exc_info.value.detail["message"] == "Content blocked by security policy"
    assert "policy_action" not in exc_info.value.detail
    assert "flags" not in exc_info.value.detail


# Mock SafeDocument for testing
def create_mock_document():
    """Create a mock SafeDocument for testing"""
    return SafeDocument(
        markdown="# Example Domain\n\nThis domain is for use in illustrative examples.",
        metadata={
            "url": "https://example.com",
            "final_url": "https://example.com",
            "title": "Example Domain",
            "fetch_time_ms": 250.0,
            "status_code": 200,
            "model": "gpt-4",
            "mode": "fast",
            "strict": True,
            "token_savings": {"percentage_saved": 95.2},
            "risk_level": "LOW",
            "structural_hash": "sha256:def456",
            "retry_attempts": 1,
            "output_profile": "rag_chunkable",
            "output_formats": ["markdown", "blocks", "chunks"],
            "chunking_strategy": "heading",
        },
        token_estimate=150,
        content_hash="sha256:abc123",
        injection_score=0.0,
        flags=[],
        removed_elements={"tags": {}, "hidden_elements": 0},
        structured_blocks=[
            {
                "block_type": "heading",
                "text": "Example Domain",
                "markdown": "# Example Domain\n",
                "ordinal": 0,
                "level": 1,
                "structural_hash": "sha256:block1",
                "metadata": {"tag": "h1"},
            }
        ],
        chunks=[
            {
                "chunk_id": "chunk-1",
                "text": "Example Domain",
                "markdown": "# Example Domain\n",
                "block_ordinals": [0],
                "structural_hash": "sha256:chunk1",
                "token_estimate": 12,
                "char_start": 0,
                "char_end": 14,
                "metadata": {"strategy": "heading"},
            }
        ],
        security_explanation={
            "scan_method": "basic",
            "recommendation": "allow",
            "summary": "No risky patterns detected.",
            "triggers": [],
            "hidden_content_detected": False,
        },
        observability={
            "stage_timings_ms": {"fetch_fast": 1.2, "security": 0.8},
            "policy_action": "allow",
            "cost_units_used": 1,
        },
    )


def create_mock_security_report():
    """Create a mock SecurityReport for testing"""
    return SecurityReport(
        injection_score=0.0,
        risk_level="LOW",
        pattern_matches=[{"pattern": "test", "weight": 0.5, "occurrences": 1, "samples": ["test"]}],
        flags=[],
        hidden_content_detected=False,
        hidden_elements_count=0,
        imperative_density=0.0,
        url="https://example.com",
        title="Example Domain",
        token_estimate=150,
        token_reduction_percent=95.2,
        original_size_bytes=1024,
        cleaned_size_bytes=256,
        content_hash="sha256:abc123",
        structural_hash="sha256:def456",
        removed_elements={"tags": {}, "hidden_elements": 0},
        explanation={
            "scan_method": "basic",
            "recommendation": "allow",
            "summary": "No risky patterns detected.",
            "triggers": [],
            "hidden_content_detected": False,
        },
        observability={
            "stage_timings_ms": {"security": 0.8},
            "policy_action": "allow",
            "cost_units_used": 1,
        },
    )


def test_root_endpoint():
    """Security fix (S5): root endpoint must not expose internal endpoint list."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "0.8.0"
    assert data["name"] == "markdown-ingress"
    assert data["docs"] == "/docs"
    # Must NOT leak endpoint inventory / internal metadata
    assert "endpoints" not in data
    assert "message" not in data


def test_health_endpoint(monkeypatch):
    """Security fix (S5): public /health must not expose job_queue paths."""

    class OpenQueue:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

    monkeypatch.setattr(api_server, "JOB_QUEUE", OpenQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "0.8.0"
    assert data["service"] == "MarkDownIngress API"
    # Must NOT expose job_queue internals on public endpoint
    assert "job_queue" not in data


def test_health_detailed_requires_api_key(monkeypatch):
    """Security fix (S5): /health/detailed must require auth."""
    monkeypatch.setattr(api_server, "OPTIONAL_API_KEY", "secret-test-key")
    monkeypatch.setattr(api_server, "API_KEY_CONFIG_ERROR", False)

    response = client.get("/api/v1/health/detailed")
    assert response.status_code == 401

    ok = client.get("/api/v1/health/detailed", headers={"x-api-key": "secret-test-key"})
    assert ok.status_code == 200
    data = ok.json()
    assert "job_queue" in data


def test_rate_limit_cleanup_evicts_oldest_clients_first(monkeypatch):
    now = time.time()
    monkeypatch.setattr(
        api_server,
        "_request_counts",
        {
            "old": [now - 59],
            "mid": [now - 30],
            "new": [now - 1],
        },
    )
    monkeypatch.setattr(api_server, "_RATE_LIMIT_MAX_CLIENTS", 2)
    monkeypatch.setattr(
        api_server,
        "_rate_limit_cleanup_counter",
        api_server._RATE_LIMIT_CLEANUP_THRESHOLD - 1,
    )

    allowed, retry_after = api_server._check_rate_limit("trigger")

    assert allowed is True
    assert retry_after == 0
    # After adding "trigger" (4 clients) and evicting down to max=2,
    # the two least recently active clients remain: "new" and "trigger"
    assert sorted(api_server._request_counts) == ["new", "trigger"]


def test_redis_rate_limit_client_initializes_once_under_concurrency(monkeypatch):
    import markdown_ingress.api_server_auth as auth

    created_clients: list[object] = []

    class FakeClient:
        def ping(self):
            time.sleep(0.01)

    class FakeRedis:
        @staticmethod
        def from_url(url, decode_responses=True):
            client = FakeClient()
            created_clients.append(client)
            time.sleep(0.01)
            return client

    fake_redis = types.SimpleNamespace(Redis=FakeRedis)
    monkeypatch.setitem(sys.modules, "redis", fake_redis)
    monkeypatch.setattr(auth, "_rate_limit_redis_client", None)

    worker_count = 8
    start = threading.Barrier(worker_count)
    results: list[object] = []
    errors: list[BaseException] = []

    def load_client():
        try:
            start.wait()
            results.append(auth._get_redis_rate_limit_client())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=load_client) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == worker_count
    assert len({id(result) for result in results}) == 1
    assert len(created_clients) == 1


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_basic(mock_ingest):
    """Test basic ingestion endpoint with fast mode"""
    mock_ingest.return_value = create_mock_document()

    response = client.post(
        "/ingest",
        json={"url": "https://example.com", "mode": "fast", "strict": True, "timeout": 30},
    )
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "markdown" in data
    assert "metadata" in data
    assert "token_estimate" in data
    assert "injection_score" in data
    assert "flags" in data
    assert "content_hash" in data
    assert "removed_elements" in data

    # Check data types
    assert isinstance(data["markdown"], str)
    assert isinstance(data["metadata"], dict)
    assert isinstance(data["token_estimate"], int)
    assert isinstance(data["injection_score"], float)
    assert isinstance(data["flags"], list)
    assert isinstance(data["content_hash"], str)

    # Check injection score range
    assert 0.0 <= data["injection_score"] <= 1.0

    # New structured / explainability fields
    assert data["structured_blocks"][0]["block_type"] == "heading"
    assert data["chunks"][0]["metadata"]["strategy"] == "heading"
    assert data["security_explanation"]["scan_method"] == "basic"
    assert data["observability"]["policy_action"] == "allow"


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_auto_mode(mock_ingest):
    """Test ingestion with auto mode"""
    mock_ingest.return_value = create_mock_document()

    response = client.post("/ingest", json={"url": "https://example.com", "mode": "auto"})
    assert response.status_code == 200
    data = response.json()
    assert "markdown" in data
    assert data["metadata"]["mode"] in ["fast", "render"]


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_returns_403_for_policy_block(mock_ingest):
    blocked_doc = create_mock_document()
    blocked_doc.metadata["policy_action"] = "block"
    blocked_doc.flags = blocked_doc.flags + ["policy_block"]
    mock_ingest.side_effect = PolicyBlockedError("Blocked by policy", document=blocked_doc)

    response = client.post(
        "/api/v1/ingest",
        json={
            "url": "https://example.com",
            "mode": "fast",
        },
    )

    # Security fix (S12): 403 must not leak flags or policy_action.
    assert response.status_code == 403
    data = response.json()
    assert data["detail"]["type"] == "policy_blocked"
    assert data["detail"]["message"] == "Content blocked by security policy"
    assert "policy_action" not in data["detail"]
    assert "flags" not in data["detail"]


@patch("markdown_ingress.api_server.ingest")
def test_versioned_ingest_endpoint_forwards_profiles_and_domain_policies(mock_ingest):
    mock_ingest.return_value = create_mock_document()

    response = client.post(
        "/api/v1/ingest",
        json={
            "url": "https://docs.example.com/guide",
            "mode": "fast",
            "output_format": "json",
            "output_profile": "rag_chunkable",
            "output_formats": ["markdown", "blocks", "chunks"],
            "save_reports": True,
            "reports_dir": "saved-reports",
            "fetcher_user_agent": "UA/1.0",
            "domain_request_interval": 0.5,
            "circuit_breaker_threshold": 7,
            "circuit_breaker_open_seconds": 12.5,
            "extract_blocks": True,
            "chunking_strategy": "heading",
            "chunk_size": 900,
            "chunk_overlap": 80,
            "render_cost_budget": 15,
            "domain_policies": [
                {
                    "domain": "docs.example.com",
                    "include_subdomains": True,
                    "output_profile": "llm_safe",
                    "policy_name": "strict",
                    "block_threshold": 0.2,
                    "warn_threshold": 0.1,
                    "request_interval": 1.0,
                    "notes": "Docs profile",
                }
            ],
        },
    )

    assert response.status_code == 200
    mock_ingest.assert_called_once()
    kwargs = mock_ingest.call_args.kwargs
    # allow_local_urls is server-side only now; handler must not forward it.
    assert "allow_local_urls" not in kwargs
    assert kwargs["output_format"] == "json"
    assert kwargs["output_profile"] == "rag_chunkable"
    assert kwargs["output_formats"] == ["markdown", "blocks", "chunks"]
    assert kwargs["save_reports"] is True
    assert kwargs["reports_dir"] == "saved-reports"
    assert kwargs["fetcher_user_agent"] == "UA/1.0"
    assert kwargs["domain_request_interval"] == 0.5
    assert kwargs["circuit_breaker_threshold"] == 7
    assert kwargs["circuit_breaker_open_seconds"] == 12.5
    assert kwargs["extract_blocks"] is True
    assert kwargs["chunking_strategy"] == "heading"
    assert kwargs["chunk_size"] == 900
    assert kwargs["chunk_overlap"] == 80
    assert kwargs["detect_language"] is True
    assert kwargs["normalize_multilingual"] is True
    assert kwargs["include_security_explanation"] is True
    assert kwargs["include_observability"] is True
    assert kwargs["render_cost_budget"] == 15
    assert kwargs["domain_policies"] == [
        {
            "domain": "docs.example.com",
            "include_subdomains": True,
            "output_profile": "llm_safe",
            "policy_name": "strict",
            "block_threshold": 0.2,
            "warn_threshold": 0.1,
            "request_interval": 1.0,
            "notes": "Docs profile",
        }
    ]


def test_ingest_endpoint_invalid_mode():
    """Test ingestion with invalid mode parameter"""
    response = client.post("/ingest", json={"url": "https://example.com", "mode": "invalid_mode"})
    assert response.status_code == 422  # Validation error


def test_ingest_endpoint_invalid_url():
    """Test ingestion with invalid URL"""
    response = client.post("/ingest", json={"url": "not-a-valid-url", "mode": "fast"})
    assert response.status_code == 422  # Validation error


def test_api_server_models_honor_env_limits(monkeypatch):
    monkeypatch.setenv("MDI_API_MAX_BATCH_URLS", "1")
    monkeypatch.setenv("MDI_API_MAX_TIMEOUT", "42")
    monkeypatch.setenv("MDI_API_MAX_CHUNK_SIZE", "321")

    from pydantic import ValidationError

    import markdown_ingress.api_server_models as api_server_models

    reloaded = importlib.reload(api_server_models)
    try:
        with pytest.raises(ValidationError):
            reloaded.BatchIngestRequest(
                urls=["https://example.com", "https://example.org"],
                mode="fast",
            )

        with pytest.raises(ValidationError):
            reloaded.IngestRequest(
                url="https://example.com",
                timeout=43,
                mode="fast",
            )

        with pytest.raises(ValidationError):
            reloaded.BatchIngestRequest(
                urls=["https://example.com"],
                chunk_size=322,
                mode="fast",
            )
    finally:
        monkeypatch.undo()
        importlib.reload(api_server_models)


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_with_stealth(mock_ingest):
    """Test ingestion with stealth mode enabled"""
    mock_ingest.return_value = create_mock_document()

    response = client.post(
        "/ingest", json={"url": "https://example.com", "mode": "fast", "stealth": True}
    )
    assert response.status_code == 200


@patch("markdown_ingress.api_server.retry_ingest")
def test_retry_ingest_endpoint(mock_retry):
    """Test retry ingestion endpoint"""
    mock_retry.return_value = create_mock_document()

    response = client.post(
        "/ingest/retry",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "max_retries": 2,
            "initial_timeout": 30.0,
        },
    )
    assert response.status_code == 200
    data = response.json()

    # Should have retry metadata
    assert "metadata" in data
    assert "retry_attempts" in data["metadata"]
    assert data["metadata"]["retry_attempts"] >= 1


@patch("markdown_ingress.api_server.retry_ingest")
def test_retry_ingest_endpoint_rejects_client_allow_local_urls(mock_retry, monkeypatch):
    """Security fix (S1): client cannot supply allow_local_urls; server-side env only."""
    mock_retry.return_value = create_mock_document()
    monkeypatch.setenv("MDI_ALLOW_LOCAL_URLS", "true")

    # Client-supplied field must be rejected (extra=forbid).
    response_rejected = client.post(
        "/ingest/retry",
        json={"url": "http://127.0.0.1:8000", "mode": "fast", "allow_local_urls": True},
    )
    assert response_rejected.status_code == 422

    # Without the field, env var MDI_ALLOW_LOCAL_URLS opts in server-side.
    response_ok = client.post(
        "/ingest/retry",
        json={"url": "http://127.0.0.1:8000", "mode": "fast"},
    )
    assert response_ok.status_code == 200
    assert "allow_local_urls" not in mock_retry.call_args.kwargs


@patch("markdown_ingress.api_server.retry_ingest")
def test_retry_ingest_endpoint_rejects_max_timeout_below_initial_timeout(mock_retry):
    response = client.post(
        "/api/v1/ingest/retry",
        json={
            "url": "https://example.com",
            "initial_timeout": 60.0,
            "max_timeout": 10.0,
        },
    )

    assert response.status_code == 422
    mock_retry.assert_not_called()


@patch("markdown_ingress.api_server.retry_ingest")
def test_retry_ingest_endpoint_does_not_misclassify_generic_import_error(mock_retry):
    mock_retry.side_effect = ImportError("No module named optional_plugin")

    response = client.post(
        "/api/v1/ingest/retry",
        json={"url": "https://example.com"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


@patch("markdown_ingress.api_server.retry_ingest")
def test_retry_ingest_endpoint_returns_400_for_playwright_import_error_message(mock_retry):
    mock_retry.side_effect = ImportError(
        "Playwright is not installed. Install with: pip install 'markdown-ingress[render]'"
    )

    response = client.post(
        "/api/v1/ingest/retry",
        json={"url": "https://example.com", "mode": "render"},
    )

    assert response.status_code == 400
    assert "Playwright" in response.json()["detail"]


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_returns_400_for_invalid_url_error(mock_ingest):
    mock_ingest.side_effect = httpx.InvalidURL("bad url")

    response = client.post(
        "/api/v1/ingest",
        json={"url": "https://example.com", "mode": "fast"},
    )

    assert response.status_code == 400
    assert "bad url" in response.json()["detail"]


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_returns_400_for_runtime_validation_error(mock_ingest):
    mock_ingest.side_effect = ValueError(
        "chunk_overlap must be less than chunk_size, got 200 >= 100"
    )

    response = client.post(
        "/api/v1/ingest",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "chunk_size": 100,
            "chunk_overlap": 200,
        },
    )

    # Security fix (S3): ValueError detail is masked to avoid leaking internal state.
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid request"


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_returns_429_for_domain_circuit_open(mock_ingest):
    mock_ingest.side_effect = DomainCircuitOpenError("Circuit breaker open for host: example.com")

    response = client.post(
        "/api/v1/ingest",
        json={"url": "https://example.com", "mode": "fast"},
    )

    assert response.status_code == 429
    assert "Circuit breaker open" in response.json()["detail"]


@patch("markdown_ingress.api_server.retry_ingest")
def test_retry_ingest_endpoint_returns_403_for_policy_block(mock_retry):
    blocked_doc = create_mock_document()
    blocked_doc.metadata["policy_action"] = "block"
    blocked_doc.flags = blocked_doc.flags + ["policy_block"]
    mock_retry.side_effect = PolicyBlockedError("Blocked by policy", document=blocked_doc)

    response = client.post(
        "/api/v1/ingest/retry",
        json={"url": "https://example.com", "mode": "fast"},
    )

    # Security fix (S12): no policy_action/flags in response.
    assert response.status_code == 403
    data = response.json()
    assert data["detail"]["type"] == "policy_blocked"
    assert data["detail"]["message"] == "Content blocked by security policy"
    assert "policy_action" not in data["detail"]


@patch("markdown_ingress.api_server.retry_ingest")
def test_retry_ingest_endpoint_forwards_max_timeout(mock_retry):
    mock_retry.return_value = create_mock_document()

    response = client.post(
        "/api/v1/ingest/retry",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "initial_timeout": 30.0,
            "max_timeout": 42.0,
        },
    )

    assert response.status_code == 200
    assert mock_retry.call_args.kwargs["max_timeout"] == 42.0


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_ingest_endpoint(mock_ingest_many):
    """Test batch ingestion endpoint"""

    class FakeBatchResult:
        successful = 2
        failed = 0
        documents = [create_mock_document(), create_mock_document()]
        errors = {}

    mock_ingest_many.return_value = FakeBatchResult()

    response = client.post(
        "/ingest/batch",
        json={
            "urls": ["https://example.com", "https://example.org"],
            "mode": "fast",
            "timeout": 30,
        },
    )
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "results" in data
    assert "success_count" in data
    assert "failure_count" in data

    # Check results
    assert isinstance(data["results"], list)
    assert len(data["results"]) == 2

    # Each result should have url and success fields
    for result in data["results"]:
        assert "url" in result
        assert "success" in result
        if result["success"]:
            assert "data" in result
        else:
            assert "error" in result


@patch("markdown_ingress.api_server.ingest_many")
def test_legacy_batch_alias_matches_versioned_endpoint(mock_ingest_many):
    class FakeBatchResult:
        successful = 1
        failed = 1
        documents = [create_mock_document(), None]
        errors = {"https://example.org/": "timeout"}

    mock_ingest_many.return_value = FakeBatchResult()

    payload = {
        "urls": ["https://example.com", "https://example.org"],
        "mode": "fast",
        "output_profile": "rag_chunkable",
        "extract_blocks": True,
        "chunking_strategy": "heading",
        "max_concurrent": 2,
    }

    legacy = client.post("/ingest/batch", json=payload)
    versioned = client.post("/api/v1/ingest/batch", json=payload)

    assert legacy.status_code == 200
    assert versioned.status_code == 200
    assert legacy.json() == versioned.json()


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_endpoint_preserves_duplicate_url_errors_by_position(mock_ingest_many):
    class FakeBatchResult:
        successful = 0
        failed = 2
        documents = [None, None]
        errors = {"https://same.test/": "err-1"}
        error_items = [
            BatchErrorItem(index=0, url="https://same.test/", error="err-1"),
            BatchErrorItem(index=1, url="https://same.test/", error="err-2"),
        ]

    mock_ingest_many.return_value = FakeBatchResult()

    response = client.post(
        "/ingest/batch",
        json={"urls": ["https://same.test", "https://same.test"], "mode": "fast"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["error"] == "err-1"
    assert data["results"][1]["error"] == "err-2"


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_endpoint_legacy_errors_by_url_preserve_duplicate_order(mock_ingest_many):
    class FakeBatchResult:
        successful = 0
        failed = 2
        documents = [None, None]
        errors = []
        errors_by_url = {
            "https://same.test/": ["err-1", "err-2"],
        }

    mock_ingest_many.return_value = FakeBatchResult()

    response = client.post(
        "/ingest/batch",
        json={"urls": ["https://same.test", "https://same.test"], "mode": "fast"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["error"] == "err-1"
    assert data["results"][1]["error"] == "err-2"


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_endpoint_synthesizes_missing_rows_for_short_batch_result(mock_ingest_many):
    class FakeBatchResult:
        successful = 1
        failed = 0
        documents = [create_mock_document()]
        errors = []

    mock_ingest_many.return_value = FakeBatchResult()

    response = client.post(
        "/ingest/batch",
        json={"urls": ["https://example.com", "https://missing.example"], "mode": "fast"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 2
    assert data["success_count"] == 1
    assert data["failure_count"] == 1
    assert data["results"][0]["success"] is True
    assert data["results"][1]["success"] is False
    assert data["results"][1]["url"] == "https://missing.example/"
    assert data["results"][1]["error"] == "Missing batch result for input index 1"


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_endpoint_ignores_extra_documents_beyond_requested_urls(mock_ingest_many):
    class FakeBatchResult:
        successful = 2
        failed = 0
        documents = [create_mock_document(), create_mock_document()]
        errors = []

    mock_ingest_many.return_value = FakeBatchResult()

    response = client.post(
        "/ingest/batch",
        json={"urls": ["https://example.com"], "mode": "fast"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["success_count"] == 1
    assert data["failure_count"] == 0
    assert data["results"][0]["url"] == "https://example.com/"


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_endpoint_forwards_output_flags(mock_ingest_many):
    class FakeBatchResult:
        successful = 1
        failed = 0
        documents = [create_mock_document()]
        errors = []

    mock_ingest_many.return_value = FakeBatchResult()

    response = client.post(
        "/api/v1/ingest/batch",
        json={
            "urls": ["https://example.com"],
            "mode": "fast",
            "detect_language": False,
            "normalize_multilingual": False,
            "include_security_explanation": False,
            "include_observability": False,
            "auto_render_threshold": 77,
            "stealth": True,
            "disable_http2": True,
            "extreme_mode": True,
            "screenshot": True,
            "extract_metadata": False,
            "extract_links": False,
            "advanced_security": True,
            "use_llm": True,
            "policy_name": "strict",
            "custom_patterns": ["secret"],
            "output_format": "json",
            "output_profile": "llm_safe",
            "output_formats": ["markdown", "security"],
            "save_reports": True,
            "reports_dir": "saved-reports",
            "fetcher_user_agent": "UA/1.0",
            "domain_request_interval": 0.5,
            "circuit_breaker_threshold": 7,
            "circuit_breaker_open_seconds": 12.5,
            "render_cost_budget": 9,
            "domain_policies": [
                {
                    "domain": "docs.example.com",
                    "mode": "render",
                    "render_cost_budget": 7,
                }
            ],
        },
    )

    assert response.status_code == 200
    kwargs = mock_ingest_many.call_args.kwargs
    # allow_local_urls removed from client-controllable params
    assert "allow_local_urls" not in kwargs
    assert kwargs["auto_render_threshold"] == 77
    assert kwargs["stealth"] is True
    assert kwargs["disable_http2"] is True
    assert kwargs["extreme_mode"] is True
    assert kwargs["screenshot"] is True
    assert kwargs["extract_metadata"] is False
    assert kwargs["extract_links"] is False
    assert kwargs["advanced_security"] is True
    assert kwargs["use_llm"] is True
    assert kwargs["policy_name"] == "strict"
    assert kwargs["custom_patterns"] == ["secret"]

    assert kwargs["output_format"] == "json"
    assert kwargs["output_profile"] == "llm_safe"
    assert kwargs["output_formats"] == ["markdown", "security"]
    assert kwargs["detect_language"] is False
    assert kwargs["normalize_multilingual"] is False
    assert kwargs["include_security_explanation"] is False
    assert kwargs["include_observability"] is False
    assert kwargs["save_reports"] is True
    assert kwargs["reports_dir"] == "saved-reports"
    assert kwargs["fetcher_user_agent"] == "UA/1.0"
    assert kwargs["domain_request_interval"] == 0.5
    assert kwargs["circuit_breaker_threshold"] == 7
    assert kwargs["circuit_breaker_open_seconds"] == 12.5
    assert kwargs["render_cost_budget"] == 9
    assert kwargs["domain_policies"] == [
        {
            "domain": "docs.example.com",
            "include_subdomains": True,
            "mode": "render",
            "render_cost_budget": 7,
        }
    ]


@patch("markdown_ingress.api_server.generate_security_report")
def test_versioned_security_report_endpoint_exposes_explanation_and_observability(mock_report):
    mock_report.return_value = create_mock_security_report()

    response = client.post(
        "/api/v1/security/report",
        json={"url": "https://example.com", "mode": "fast"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["explanation"]["scan_method"] == "basic"
    assert data["observability"]["policy_action"] == "allow"


@patch("markdown_ingress.api_server.generate_security_report")
def test_security_report_endpoint_returns_403_for_policy_block(mock_report):
    blocked_doc = create_mock_document()
    blocked_doc.metadata["policy_action"] = "block"
    blocked_doc.flags = blocked_doc.flags + ["policy_block"]
    mock_report.side_effect = PolicyBlockedError("Blocked by policy", document=blocked_doc)

    response = client.post(
        "/api/v1/security/report",
        json={"url": "https://example.com", "mode": "fast"},
    )

    # Security fix (S12): 403 must not leak flags or policy_action.
    assert response.status_code == 403
    data = response.json()
    assert data["detail"]["type"] == "policy_blocked"
    assert data["detail"]["message"] == "Content blocked by security policy"
    assert "policy_action" not in data["detail"]
    assert "flags" not in data["detail"]


@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_returns_415_for_non_html_render_target(mock_ingest):
    mock_ingest.side_effect = UnsupportedContentTypeError("non-html target")

    response = client.post(
        "/api/v1/ingest",
        json={"url": "https://example.com/file.pdf", "mode": "render"},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "non-html target"


@pytest.mark.parametrize(
    ("upstream_status", "expected_status"),
    [
        (404, 404),
        (503, 502),
    ],
)
@patch("markdown_ingress.api_server.ingest")
def test_ingest_endpoint_maps_upstream_http_status_errors(
    mock_ingest,
    upstream_status: int,
    expected_status: int,
):
    request = httpx.Request("GET", "https://example.com/missing")
    response = httpx.Response(upstream_status, request=request)
    mock_ingest.side_effect = httpx.HTTPStatusError(
        f"upstream status {upstream_status}",
        request=request,
        response=response,
    )

    api_response = client.post(
        "/api/v1/ingest",
        json={"url": "https://example.com/missing", "mode": "fast"},
    )

    assert api_response.status_code == expected_status
    assert api_response.json()["detail"] == "Upstream fetch returned an HTTP error"


@patch("markdown_ingress.api_server.generate_security_report")
def test_security_report_endpoint_returns_415_for_non_html_render_target(mock_report):
    mock_report.side_effect = UnsupportedContentTypeError("non-html target")

    response = client.post(
        "/api/v1/security/report",
        json={"url": "https://example.com/file.pdf", "mode": "render"},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "non-html target"


@patch("markdown_ingress.api_server.generate_security_report")
def test_security_report_endpoint_forwards_runtime_contract(mock_report):
    mock_report.return_value = create_mock_security_report()

    response = client.post(
        "/api/v1/security/report",
        json={
            "url": "https://docs.example.com/report",
            "mode": "render",
            "strict": False,
            "timeout": 45,
            "model": "gpt-4.1",
            "auto_render_threshold": 77,
            "stealth": True,
            "disable_http2": True,
            "extreme_mode": True,
            "screenshot": True,
            "extract_metadata": False,
            "extract_links": False,
            "advanced_security": True,
            "use_llm": True,
            "policy_name": "strict",
            "custom_patterns": ["secret"],
            "output_profile": "llm_safe",
            "extract_blocks": True,
            "chunking_strategy": "heading",
            "chunk_size": 800,
            "chunk_overlap": 40,
            "detect_language": False,
            "normalize_multilingual": False,
            "include_security_explanation": False,
            "include_observability": False,
            "render_cost_budget": 9,
            "domain_policies": [
                {
                    "domain": "docs.example.com",
                    "mode": "render",
                    "render_cost_budget": 7,
                }
            ],
        },
    )

    assert response.status_code == 200
    kwargs = mock_report.call_args.kwargs
    assert kwargs["mode"] == "render"
    assert kwargs["strict"] is False
    assert kwargs["timeout"] == 45.0
    assert kwargs["model"] == "gpt-4.1"
    assert kwargs["auto_render_threshold"] == 77
    assert kwargs["stealth"] is True
    assert kwargs["disable_http2"] is True
    assert kwargs["extreme_mode"] is True
    assert kwargs["screenshot"] is True
    assert kwargs["extract_metadata"] is False
    assert kwargs["extract_links"] is False
    assert kwargs["advanced_security"] is True
    assert kwargs["use_llm"] is True
    assert kwargs["policy_name"] == "strict"
    assert kwargs["custom_patterns"] == ["secret"]

    assert kwargs["output_profile"] == "llm_safe"
    assert kwargs["extract_blocks"] is True
    assert kwargs["chunking_strategy"] == "heading"
    assert kwargs["chunk_size"] == 800
    assert kwargs["chunk_overlap"] == 40
    assert kwargs["detect_language"] is False
    assert kwargs["normalize_multilingual"] is False
    assert kwargs["include_security_explanation"] is False
    assert kwargs["include_observability"] is False
    assert kwargs["render_cost_budget"] == 9
    assert kwargs["domain_policies"] == [
        {
            "domain": "docs.example.com",
            "include_subdomains": True,
            "mode": "render",
            "render_cost_budget": 7,
        }
    ]


@patch("markdown_ingress.api_server.get_ingest_stats")
def test_versioned_stats_endpoint_returns_observability_snapshot(mock_stats):
    mock_stats.return_value = {
        "requests_total": 3,
        "mode_counts": {"fast": 2, "render": 0, "auto": 1},
    }

    response = client.get("/api/v1/stats")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "0.8.0"
    assert data["stats"]["requests_total"] == 3
    assert data["stats"]["mode_counts"]["auto"] == 1
    assert "job_queue" in data
    assert "pending_visible_total" in data["job_queue"]


@patch("markdown_ingress.api_server.get_ingest_stats")
def test_stats_endpoint_does_not_call_get_job_queue(mock_stats, monkeypatch):
    mock_stats.return_value = {"requests_total": 0}

    class ReadOnlyQueue:
        state = "open"
        db_path = "current.sqlite3"
        ttl_seconds = 777

        def pending_count(self, cleanup_expired=True):
            assert cleanup_expired is False
            return 3

    def fail_get_job_queue():
        raise AssertionError("_get_job_queue should not be called by /api/v1/stats")

    monkeypatch.setattr(api_server, "JOB_QUEUE", ReadOnlyQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "_get_job_queue", fail_get_job_queue)

    response = client.get("/api/v1/stats")

    assert response.status_code == 200
    data = response.json()
    assert data["job_queue"]["ttl_seconds"] == 777
    assert data["job_queue"]["pending"] == 3


@patch("markdown_ingress.api_server.compare_extractors")
def test_versioned_extractor_evaluation_endpoint(mock_compare):
    mock_compare.return_value = {
        "readability": {
            "available": True,
            "length": 120,
            "token_estimate": 40,
            "injection_score": 0.0,
        },
        "trafilatura": {
            "available": False,
            "length": 0,
            "token_estimate": 0,
            "injection_score": 0.0,
        },
    }

    response = client.post(
        "/api/v1/evaluate/extractors",
        json={"html": "<html><body><article><h1>X</h1></article></body></html>", "model": "gpt-4"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"]["readability"]["available"] is True
    assert data["results"]["trafilatura"]["available"] is False


class _ImmediateThread:
    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_job_polling_completes_and_returns_result(mock_ingest_many, monkeypatch, tmp_path):
    class FakeBatchResult:
        total = 2
        successful = 1
        failed = 1
        documents = [create_mock_document(), None]
        errors = {"https://example.org/": "timeout"}

    mock_ingest_many.return_value = FakeBatchResult()
    queue = api_server.PersistentJobQueue(
        str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600
    )
    monkeypatch.setattr(api_server, "JOB_QUEUE", queue)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    try:
        submit = client.post(
            "/api/v1/jobs/batch",
            json={
                "urls": ["https://example.com", "https://example.org"],
                "mode": "fast",
                "output_profile": "rag_chunkable",
                "extract_blocks": True,
                "chunking_strategy": "heading",
            },
        )

        assert submit.status_code == 200
        payload = submit.json()
        assert payload["status"] == "queued"
        assert payload["poll_url"].startswith("/api/v1/jobs/")
        assert payload["ttl_applies_to"] == "completed_jobs"

        deadline = time.time() + 5.0
        while True:
            poll = client.get(payload["poll_url"])
            assert poll.status_code == 200
            job = poll.json()
            if job["status"] == "completed" or time.time() >= deadline:
                break
            time.sleep(0.05)

        assert job["job_id"] == payload["job_id"]
        assert job["status"] == "completed"
        assert job["result"]["success_count"] == 1
        assert job["result"]["failure_count"] == 1
        assert job["result"]["results"][0]["success"] is True
        assert (
            job["result"]["results"][0]["data"]["structured_blocks"][0]["block_type"] == "heading"
        )
        assert job["result"]["results"][1]["success"] is False
        assert job["result"]["results"][1]["error"] == "timeout"
    finally:
        queue.close()


def test_batch_job_polling_returns_404_for_unknown_job(monkeypatch):
    class EmptyQueue:
        state = "open"

        def get(self, job_id, cleanup_expired=True):
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", EmptyQueue())
    monkeypatch.setattr(api_server, "_job_queue_initialized", True)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/api/v1/jobs/does-not-exist")
    assert response.status_code == 404


def test_batch_job_submit_returns_503_when_queue_unavailable(monkeypatch):
    monkeypatch.setattr(api_server, "JOB_QUEUE", None)
    monkeypatch.setattr(api_server, "_job_queue_initialized", True)

    response = client.post(
        "/api/v1/jobs/batch",
        json={"urls": ["https://example.com"], "mode": "fast"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Job queue is unavailable"


def test_batch_job_status_returns_503_when_queue_unavailable(monkeypatch):
    monkeypatch.setattr(api_server, "JOB_QUEUE", None)
    monkeypatch.setattr(api_server, "_job_queue_initialized", True)

    response = client.get("/api/v1/jobs/some-job")

    assert response.status_code == 503
    assert response.json()["detail"] == "Job queue is unavailable"


def test_batch_ingest_empty_urls():
    """Test batch ingestion with no URLs"""
    response = client.post("/ingest/batch", json={"urls": [], "mode": "fast"})
    assert response.status_code == 200
    data = response.json()
    assert data["success_count"] == 0
    assert data["failure_count"] == 0


def test_batch_ingest_too_many_urls():
    """Test batch ingestion exceeding max limit"""
    # Create 101 URLs (exceeds 100 limit)
    urls = [f"https://example{i}.com" for i in range(101)]
    response = client.post("/ingest/batch", json={"urls": urls, "mode": "fast"})
    assert response.status_code == 422  # Validation error


def test_legacy_aliases_require_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("MDI_API_KEY", "secret")
    import markdown_ingress.api_server as api_server_module

    reloaded = importlib.reload(api_server_module)
    secured_client = TestClient(reloaded.app)
    try:
        requests = [
            ("POST", "/api/v1/ingest", {"url": "https://example.com", "mode": "fast"}),
            ("POST", "/ingest", {"url": "https://example.com", "mode": "fast"}),
            ("POST", "/api/v1/ingest/retry", {"url": "https://example.com"}),
            ("POST", "/ingest/retry", {"url": "https://example.com"}),
            ("POST", "/api/v1/ingest/batch", {"urls": ["https://example.com"], "mode": "fast"}),
            ("POST", "/ingest/batch", {"urls": ["https://example.com"], "mode": "fast"}),
            ("POST", "/api/v1/security/report", {"url": "https://example.com", "mode": "fast"}),
            ("POST", "/security/report", {"url": "https://example.com", "mode": "fast"}),
            ("POST", "/api/v1/evaluate/extractors", {"html": "<html></html>"}),
            ("POST", "/evaluate/extractors", {"html": "<html></html>"}),
        ]

        for method, path, payload in requests:
            response = secured_client.request(method, path, json=payload)
            assert response.status_code == 401, path
    finally:
        monkeypatch.delenv("MDI_API_KEY", raising=False)
        importlib.reload(api_server_module)


@patch("markdown_ingress.api_server.generate_security_report")
def test_security_report_endpoint(mock_report):
    """Test security report generation endpoint"""
    mock_report.return_value = create_mock_security_report()

    response = client.post(
        "/security/report",
        json={"url": "https://example.com", "mode": "fast", "strict": True, "timeout": 30},
    )
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "injection_score" in data
    assert "risk_level" in data
    assert "pattern_matches" in data
    assert "flags" in data
    assert "hidden_content_detected" in data
    assert "hidden_elements_count" in data
    assert "imperative_density" in data
    assert "url" in data
    assert "title" in data
    assert "token_estimate" in data
    assert "token_reduction_percent" in data
    assert "original_size_bytes" in data
    assert "cleaned_size_bytes" in data
    assert "content_hash" in data
    assert "structural_hash" in data
    assert "removed_elements" in data

    # Check data types
    assert isinstance(data["injection_score"], float)
    assert isinstance(data["risk_level"], str)
    assert isinstance(data["pattern_matches"], list)
    assert isinstance(data["flags"], list)
    assert isinstance(data["hidden_content_detected"], bool)
    assert isinstance(data["hidden_elements_count"], int)
    assert isinstance(data["imperative_density"], float)

    # Check valid risk levels
    assert data["risk_level"] in ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]


@patch("markdown_ingress.api_server.ingest")
def test_timeout_validation(mock_ingest):
    """Test timeout parameter validation"""
    mock_ingest.return_value = create_mock_document()

    # Too low
    response = client.post(
        "/ingest", json={"url": "https://example.com", "mode": "fast", "timeout": 0}
    )
    assert response.status_code == 422

    # Too high
    response = client.post(
        "/ingest", json={"url": "https://example.com", "mode": "fast", "timeout": 400}
    )
    assert response.status_code == 422

    # Valid range
    response = client.post(
        "/ingest", json={"url": "https://example.com", "mode": "fast", "timeout": 60}
    )
    assert response.status_code == 200


@patch("markdown_ingress.api_server.retry_ingest")
def test_max_retries_validation(mock_retry):
    """Test max_retries parameter validation"""
    mock_retry.return_value = create_mock_document()

    # Too low
    response = client.post("/ingest/retry", json={"url": "https://example.com", "max_retries": 0})
    assert response.status_code == 422

    # Too high
    response = client.post("/ingest/retry", json={"url": "https://example.com", "max_retries": 15})
    assert response.status_code == 422

    # Valid range
    response = client.post("/ingest/retry", json={"url": "https://example.com", "max_retries": 3})
    assert response.status_code == 200


def test_openapi_docs():
    """Test that OpenAPI docs are accessible"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi_schema = response.json()

    # Check basic OpenAPI structure
    assert "openapi" in openapi_schema
    assert "info" in openapi_schema
    assert "paths" in openapi_schema

    # Check API info
    assert openapi_schema["info"]["title"] == "MarkDownIngress API"
    assert openapi_schema["info"]["version"] == "0.8.0"

    # Check endpoints exist
    assert "/ingest" in openapi_schema["paths"]
    assert "/ingest/retry" in openapi_schema["paths"]
    assert "/ingest/batch" in openapi_schema["paths"]
    assert "/security/report" in openapi_schema["paths"]
    assert "/health" in openapi_schema["paths"]


def test_init_job_queue_reuses_previous_queue_while_close_is_still_draining():
    class ClosingQueue:
        state = "open"

        def close(self):
            self.state = "closing"
            raise RuntimeError("Job queue workers did not stop before lease release")

    previous = ClosingQueue()
    reused = _init_job_queue(previous)

    assert reused is previous
    assert reused.state == "closing"


def test_get_job_queue_replaces_previous_queue_after_close_succeeds(monkeypatch):
    class ClosingQueue:
        def __init__(self):
            self.state = "closing"
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            self.state = "closed"

    class FreshQueue:
        def __init__(self, **kwargs):
            self.state = "open"
            self.kwargs = kwargs

    previous = ClosingQueue()

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", previous)
    monkeypatch.setattr("markdown_ingress.api_server.PersistentJobQueue", FreshQueue)

    resolved = _get_job_queue()

    assert isinstance(resolved, FreshQueue)
    assert previous.close_calls == 1


def test_job_queue_repair_loop_recreates_queue_with_latest_config(monkeypatch):
    class ClosingQueue:
        def __init__(self):
            self.state = "closing"
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            if self.close_calls < 2:
                raise RuntimeError("Job queue workers did not stop before lease release")
            self.state = "closed"

    created = {}

    class FreshQueue:
        def __init__(self, **kwargs):
            self.state = "open"
            created.update(kwargs)

    previous = ClosingQueue()

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", previous)
    monkeypatch.setattr("markdown_ingress.api_server.PersistentJobQueue", FreshQueue)
    monkeypatch.setattr("markdown_ingress.api_server.JOB_DB_PATH", "new-jobs.sqlite3")
    monkeypatch.setattr("markdown_ingress.api_server.JOB_WORKERS", 7)
    monkeypatch.setattr("markdown_ingress.api_server.JOB_TTL_SECONDS", 7200)
    monkeypatch.setattr("markdown_ingress.api_server.MAX_QUEUED_JOBS", 55)

    _start_job_queue_repair_loop()

    deadline = time.time() + 5.0
    while time.time() < deadline:
        current = _get_job_queue()
        if isinstance(current, FreshQueue):
            break
        time.sleep(0.05)
    else:
        pytest.fail("job queue repair loop did not recreate the queue")

    assert created["db_path"] == "new-jobs.sqlite3"
    assert created["worker_count"] == 7
    assert created["ttl_seconds"] == 7200
    assert created["max_queued_jobs"] == 55


def test_get_job_queue_replaces_lease_lost_queue(monkeypatch):
    class LeaseLostQueue:
        state = "lease_lost"

        def close(self):
            self.state = "closed"

    class FreshQueue:
        def __init__(self, **kwargs):
            self.state = "open"

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", LeaseLostQueue())
    monkeypatch.setattr("markdown_ingress.api_server.PersistentJobQueue", FreshQueue)

    resolved = _get_job_queue()

    assert isinstance(resolved, FreshQueue)


def test_get_job_queue_returns_external_owner_queue_and_starts_repair(monkeypatch):
    class ExternalOwnerQueue:
        state = "external_owner"
        db_path = "jobs.sqlite3"

        def close(self):
            return None

    current_queue = ExternalOwnerQueue()
    repair_started = {"called": False}

    def fake_start_repair():
        repair_started["called"] = True

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", current_queue)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(
        "markdown_ingress.api_server._start_job_queue_repair_loop", fake_start_repair
    )

    resolved = _get_job_queue()

    assert resolved is current_queue
    assert repair_started["called"] is True


def test_start_job_queue_repair_loop_is_singleton_under_concurrent_calls(monkeypatch):
    started = {"count": 0}
    real_thread = threading.Thread

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
            self._alive = False

        def start(self):
            started["count"] += 1
            self._alive = True
            time.sleep(0.05)

        def is_alive(self):
            return self._alive

    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server.threading, "Thread", FakeThread)

    threads = [
        real_thread(target=api_server._start_job_queue_repair_loop),
        real_thread(target=api_server._start_job_queue_repair_loop),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert started["count"] == 1


def test_get_job_queue_keeps_existing_external_owner_wrapper_while_backend_still_busy(monkeypatch):
    class ExternalOwnerQueue:
        state = "external_owner"
        db_path = "jobs.sqlite3"

        def close(self):
            return None

    current_queue = ExternalOwnerQueue()
    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", current_queue)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_HISTORY", [])

    def failing_build():
        raise RuntimeError("Job queue DB is already owned by another active instance")

    monkeypatch.setattr("markdown_ingress.api_server._build_job_queue", failing_build)

    resolved = _get_job_queue()

    assert resolved is current_queue
    assert api_server._JOB_QUEUE_HISTORY == []


def test_init_job_queue_degrades_when_external_owner_still_holds_db(monkeypatch):
    class ExternalOwnerQueue:
        state = "external_owner"
        db_path = "jobs.sqlite3"

        def close(self):
            return None

    previous = ExternalOwnerQueue()
    repair_started = {"called": False}

    def failing_build():
        raise RuntimeError("Job queue DB is already owned by another active instance")

    def fake_start_repair():
        repair_started["called"] = True

    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_WATCHDOG_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_WATCHDOG_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_WATCHDOG_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", previous)
    monkeypatch.setattr("markdown_ingress.api_server._build_job_queue", failing_build)
    monkeypatch.setattr(
        "markdown_ingress.api_server._start_job_queue_repair_loop", fake_start_repair
    )

    resolved = _init_job_queue(previous)

    assert getattr(resolved, "state") == "external_owner"
    assert repair_started["called"] is False


def test_init_job_queue_cold_start_degrades_to_external_owner_when_db_is_owned(monkeypatch):
    repair_started = {"called": False}

    def failing_build():
        raise RuntimeError("Job queue DB is already owned by another active instance")

    def fake_start_repair():
        repair_started["called"] = True

    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_WATCHDOG_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_WATCHDOG_STOP", None)
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_WATCHDOG_THREAD", None)
    monkeypatch.setattr("markdown_ingress.api_server._build_job_queue", failing_build)
    monkeypatch.setattr("markdown_ingress.api_server.JOB_DB_PATH", "cold-start.sqlite3")
    monkeypatch.setattr(
        "markdown_ingress.api_server._start_job_queue_repair_loop", fake_start_repair
    )

    resolved = _init_job_queue(None)

    assert getattr(resolved, "state") == "external_owner"
    assert str(getattr(resolved, "db_path")) == "cold-start.sqlite3"
    assert repair_started["called"] is False


def test_get_job_record_falls_back_to_legacy_queue_history(monkeypatch):
    class EmptyQueue:
        state = "open"

        def get(self, job_id):
            return None

    class LegacyQueue:
        def get(self, job_id):
            if job_id == "legacy-job":
                return type(
                    "Job",
                    (),
                    {
                        "job_id": "legacy-job",
                        "status": "completed",
                        "created_at": "2026-03-30T00:00:00+00:00",
                        "started_at": None,
                        "completed_at": (datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
                        "result": {"ok": True},
                        "error": None,
                        "ttl_seconds": 120,
                    },
                )()
            return None

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", EmptyQueue())
    monkeypatch.setattr("markdown_ingress.api_server._JOB_QUEUE_HISTORY", [LegacyQueue()])

    job = _get_job_record("legacy-job")

    assert job is not None
    assert job.job_id == "legacy-job"


def test_batch_job_submit_returns_503_when_queue_is_closing(monkeypatch):
    class ClosingQueue:
        state = "closing"

        def close(self):
            raise RuntimeError("Job queue workers did not stop before lease release")

        def submit(self, *args, **kwargs):
            raise RuntimeError("Job queue is closing")

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", ClosingQueue())

    response = client.post(
        "/api/v1/jobs/batch", json={"urls": ["https://example.com"], "mode": "fast"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Job queue is closing"


def test_batch_job_submit_returns_503_when_queue_lease_is_lost(monkeypatch):
    class LeaseLostQueue:
        def submit(self, *args, **kwargs):
            raise RuntimeError(
                "Job queue lease was lost; this instance can no longer accept or execute jobs"
            )

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", LeaseLostQueue())

    response = client.post(
        "/api/v1/jobs/batch", json={"urls": ["https://example.com"], "mode": "fast"}
    )

    assert response.status_code == 503
    assert "lease was lost" in response.json()["detail"]


def test_ingest_endpoint_does_not_leak_internal_exception_details(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr("markdown_ingress.api_server.ingest", boom)

    response = client.post("/api/v1/ingest", json={"url": "https://example.com", "mode": "fast"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_require_api_key_rejects_empty_config(monkeypatch):
    monkeypatch.setattr(api_server, "API_KEY_CONFIG_ERROR", True)
    monkeypatch.setattr(api_server, "OPTIONAL_API_KEY", None)

    response = client.post("/api/v1/ingest", json={"url": "https://example.com", "mode": "fast"})

    assert response.status_code == 500
    assert "configuration" in response.json()["detail"].lower()


def test_rate_limit_uses_client_ip_for_anonymous_requests():
    request = Request({"type": "http", "client": ("203.0.113.10", 12345), "headers": []})

    assert api_server._rate_limit_client_id(request, None) == "ip:203.0.113.10"


def test_detect_multiworker_environment_ignores_invalid_worker_counts(monkeypatch, caplog):
    monkeypatch.setenv("GUNICORN_WORKERS", "not-an-int")
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)

    with caplog.at_level(logging.WARNING):
        assert api_server._detect_multiworker_environment() is False

    assert "Invalid integer for GUNICORN_WORKERS" in caplog.text


def test_ensure_job_queue_initialized_retries_after_failed_attempt(monkeypatch):
    calls = {"count": 0}

    class HealthyQueue:
        state = "open"
        ttl_seconds = 60
        max_queued_jobs = 10
        db_path = "jobs.sqlite3"

        def pending_count(self, cleanup_expired=False):
            return 0

    def flaky_init(previous):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("db unavailable")
        return HealthyQueue()

    monkeypatch.setattr(api_server, "JOB_QUEUE", None)
    monkeypatch.setattr(api_server, "_job_queue_initialized", False)
    monkeypatch.setattr(api_server, "_init_job_queue", flaky_init)
    monkeypatch.setattr(api_server, "_maybe_start_job_queue_repair", lambda: None)
    monkeypatch.setattr(api_server, "_start_job_queue_watchdog", lambda: None)

    api_server._ensure_job_queue_initialized()
    assert api_server.JOB_QUEUE is None
    # After failure, _job_queue_initialized remains False so transient
    # failures can be retried on the next request.
    assert api_server._job_queue_initialized is False

    # Reset backoff so the next call retries immediately (production uses
    # a short backoff to avoid log spam).
    monkeypatch.setattr(api_server, "_job_queue_init_failed_at", None)

    # Second call should retry and succeed
    api_server._ensure_job_queue_initialized()
    assert calls["count"] == 2
    assert api_server.JOB_QUEUE is not None


def test_health_endpoint_reports_degraded_job_queue_state(monkeypatch):
    repair_started = {"called": False}

    class ClosingQueue:
        state = "closing"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 2

    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 1

    def fake_start_repair():
        repair_started["called"] = True

    monkeypatch.setattr(api_server, "JOB_QUEUE", ClosingQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "_start_job_queue_repair_loop", fake_start_repair)
    # Security fix (S5): full job_queue details moved to authenticated /detailed endpoint.
    monkeypatch.setattr(api_server, "OPTIONAL_API_KEY", None)

    response = client.get("/api/v1/health/detailed")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["job_queue"]["state"] == "closing"
    assert data["job_queue"]["pending_visible_total"] == 3
    assert data["job_queue"]["legacy_visible_queues"] == 1
    assert repair_started["called"] is False


@patch("markdown_ingress.api_server.get_ingest_stats")
def test_stats_endpoint_aggregates_current_and_legacy_job_visibility(mock_stats, monkeypatch):
    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self):
            return 2

    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def pending_count(self):
            return 3

    mock_stats.return_value = {"requests_total": 1}
    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/api/v1/stats")

    # Security fix (S10): /stats must expose only basename + count, not paths.
    assert response.status_code == 200
    jobs = response.json()["job_queue"]
    assert jobs["current_pending"] == 2
    assert jobs["legacy_pending"] == 3
    assert jobs["pending_visible_total"] == 5
    assert jobs["pending"] == 5
    assert jobs["legacy_visible_queues"] == 1
    assert jobs["current_db_name"] == "current.sqlite3"
    assert jobs["legacy_db_count"] == 1
    assert "current_db_path" not in jobs
    assert "legacy_db_paths" not in jobs
    assert jobs["ttl_applies_to"] == "completed_jobs_with_persisted_ttl_or_legacy_compatibility_ttl"


def test_snapshot_job_subsystem_deduplicates_same_db_path(monkeypatch):
    class QueueWithPath:
        state = "open"

        def __init__(self, db_path, pending):
            self.db_path = db_path
            self._pending = pending

        def pending_count(self, cleanup_expired=True):
            return self._pending

    monkeypatch.setattr(api_server, "JOB_QUEUE", QueueWithPath("shared.sqlite3", 2))
    monkeypatch.setattr(
        api_server,
        "_JOB_QUEUE_HISTORY",
        [QueueWithPath("shared.sqlite3", 9), QueueWithPath("legacy.sqlite3", 3)],
    )
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = _snapshot_job_subsystem()

    assert snapshot["current_pending"] == 2
    assert snapshot["legacy_pending"] == 3
    assert snapshot["pending_visible_total"] == 5
    assert snapshot["legacy_visible_queues"] == 1
    assert snapshot["legacy_db_paths"] == ["legacy.sqlite3"]


def test_snapshot_job_subsystem_counts_legacy_queue_when_current_queue_is_none(monkeypatch):
    class LegacyQueue:
        state = "closed"
        db_path = "artifacts/api_jobs/jobs.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 7

    monkeypatch.setattr(api_server, "JOB_QUEUE", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = _snapshot_job_subsystem(start_repair=False)

    assert snapshot["current_state"] == "uninitialized"
    assert snapshot["legacy_pending"] == 7
    assert snapshot["pending_visible_total"] is None
    assert snapshot["legacy_visible_queues"] == 1
    assert snapshot["legacy_db_paths"] == ["artifacts/api_jobs/jobs.sqlite3"]


def test_snapshot_job_subsystem_handles_legacy_queue_without_db_path(monkeypatch):
    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 2

    class LegacyQueueWithoutPath:
        def pending_count(self, cleanup_expired=True):
            return 3

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueueWithoutPath()])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = _snapshot_job_subsystem(start_repair=False)

    assert snapshot["legacy_pending"] == 3
    assert snapshot["pending_visible_total"] == 5
    assert snapshot["legacy_visible_queues"] == 1
    assert snapshot["legacy_db_paths"] == []


def test_snapshot_job_subsystem_does_not_cleanup_expired_jobs(monkeypatch):
    class ReadOnlyQueue:
        state = "open"
        db_path = "read-only.sqlite3"

        def pending_count(self, cleanup_expired=True):
            assert cleanup_expired is False
            return 1

    monkeypatch.setattr(api_server, "JOB_QUEUE", ReadOnlyQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = _snapshot_job_subsystem()

    assert snapshot["current_pending"] == 1


def test_health_degrades_when_pending_visibility_is_unknown(monkeypatch):
    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 2

    class BrokenLegacyQueue:
        db_path = "legacy.sqlite3"

        def pending_count(self, cleanup_expired=True):
            raise RuntimeError("backend read failed")

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [BrokenLegacyQueue()])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "OPTIONAL_API_KEY", None)

    response = client.get("/api/v1/health/detailed")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["job_queue"]["pending_visible_total"] is None


def test_get_job_queue_does_not_hold_global_lock_while_close_drains(monkeypatch):
    close_started = threading.Event()
    allow_close = threading.Event()

    class ClosingQueue:
        state = "closing"
        db_path = "closing.sqlite3"

        def close(self):
            close_started.set()
            allow_close.wait(timeout=5.0)
            self.state = "closed"

        def pending_count(self, cleanup_expired=True):
            return 2

    class FreshQueue:
        def __init__(self, **kwargs):
            self.state = "open"
            self.db_path = "fresh.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

    monkeypatch.setattr(api_server, "JOB_QUEUE", ClosingQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "PersistentJobQueue", FreshQueue)

    worker = threading.Thread(target=_get_job_queue)
    worker.start()
    assert close_started.wait(timeout=5.0) is True

    snapshot = _snapshot_job_subsystem()
    assert snapshot["current_state"] == "closing"

    allow_close.set()
    worker.join(timeout=5.0)
    assert worker.is_alive() is False


def test_get_job_queue_returns_promptly_when_inline_close_cannot_finish(monkeypatch):
    class ClosingQueue:
        state = "closing"
        db_path = "closing.sqlite3"

        def close(self, inline_wait_timeout=None):
            raise RuntimeError("Job queue inline jobs did not stop before lease release")

        def pending_count(self, cleanup_expired=True):
            return 1

    monkeypatch.setattr(api_server, "JOB_QUEUE", ClosingQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    started = time.monotonic()
    queue = _get_job_queue()
    elapsed = time.monotonic() - started

    assert getattr(queue, "state") == "closing"
    assert elapsed < 0.5


def test_build_replacement_queue_returns_current_when_another_thread_already_repaired(monkeypatch):
    class ClosingQueue:
        state = "closing"
        db_path = "closing.sqlite3"

    class FreshQueue:
        state = "open"
        db_path = "fresh.sqlite3"

    current_queue = ClosingQueue()
    replacement = FreshQueue()

    monkeypatch.setattr(api_server, "JOB_QUEUE", current_queue)

    def fake_build():
        api_server.JOB_QUEUE = replacement
        return type("RacingQueue", (), {"close": lambda self: None})()

    monkeypatch.setattr(api_server, "_build_job_queue", fake_build)

    resolved = api_server._build_replacement_queue_or_current(current_queue)

    assert resolved is replacement


def test_get_job_queue_degrades_when_repair_loses_constructor_race(monkeypatch):
    class ClosingQueue:
        state = "closing"
        db_path = "closing.sqlite3"

        def close(self, inline_wait_timeout=None, preserve_state_on_inline_timeout=False):
            self.state = "closed"

        def pending_count(self, cleanup_expired=True):
            return 0

    current_queue = ClosingQueue()
    monkeypatch.setattr(api_server, "JOB_QUEUE", current_queue)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    def failing_build():
        raise RuntimeError("Job queue DB is already owned by another active instance")

    monkeypatch.setattr(api_server, "_build_job_queue", failing_build)

    resolved = _get_job_queue()

    assert resolved is not current_queue
    assert getattr(resolved, "state") == "external_owner"


def test_get_job_record_uses_api_ttl_without_cleanup_side_effects(monkeypatch):
    class LegacyQueue:
        db_path = "legacy.sqlite3"
        cleanup_called = False

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "started_at": None,
                    "completed_at": (datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": 120,
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "JOB_TTL_SECONDS", 60)

    job = _get_job_record("legacy-job")

    assert job is not None
    assert job.job_id == "legacy-job"


def test_get_job_record_keeps_legacy_job_visible_when_ttl_is_unknown_but_legacy_expiry_is_future(
    monkeypatch,
):
    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "started_at": None,
                    "completed_at": (datetime.now(UTC) - timedelta(seconds=120)).isoformat(),
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": None,
                    "legacy_expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "JOB_TTL_SECONDS", 60)

    job = _get_job_record("legacy-job")

    assert job is not None
    assert job.job_id == "legacy-job"


def test_get_job_record_keeps_legacy_job_visible_when_ttl_is_unknown_and_legacy_expiry_is_missing(
    monkeypatch,
):
    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "started_at": None,
                    "completed_at": (datetime.now(UTC) - timedelta(seconds=120)).isoformat(),
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": None,
                    "legacy_expires_at": None,
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "JOB_TTL_SECONDS", 60)

    job = _get_job_record("legacy-job")

    assert job is not None
    assert job.job_id == "legacy-job"


def test_get_job_record_treats_naive_legacy_expiry_as_utc(monkeypatch):
    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30 00:00:00",
                    "started_at": None,
                    "completed_at": "2026-03-30 00:01:00",
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": None,
                    "legacy_expires_at": "2999-01-01 00:00:00",
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])

    job = _get_job_record("legacy-job")

    assert job is not None
    assert job.job_id == "legacy-job"


def test_get_job_record_keeps_legacy_job_visible_with_corrupt_completed_at(
    monkeypatch,
):
    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "started_at": None,
                    "completed_at": "not-a-date",
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": None,
                    "legacy_expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])

    job = _get_job_record("legacy-job")

    assert job is not None
    assert job.job_id == "legacy-job"


def test_get_job_record_hides_completed_job_with_missing_completed_at_and_ttl_seconds(monkeypatch):
    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "started_at": None,
                    "completed_at": None,
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": 120,
                    "legacy_expires_at": None,
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])

    job = _get_job_record("legacy-job")

    assert job is None


def test_get_job_record_hides_completed_job_with_corrupt_ttl_seconds(monkeypatch):
    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "started_at": None,
                    "completed_at": (datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": "not-an-int",
                    "legacy_expires_at": None,
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])

    job = _get_job_record("legacy-job")

    assert job is None


def test_snapshot_job_subsystem_counts_unknown_ttl_jobs(monkeypatch):
    class QueueWithUnknownTTL:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 1

        def _connect(self):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    webhook_url TEXT,
                    ttl_seconds INTEGER,
                    legacy_expires_at TEXT
                )
                """)
            conn.execute("""
                INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
                VALUES (
                    'legacy', 'completed', '2026-03-30T00:00:00+00:00',
                    '2026-03-30T00:01:00+00:00', NULL,
                    '2999-01-01T00:00:00+00:00'
                )
                """)
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
                VALUES (
                    'legacy-missing-expiry', 'completed',
                    '2026-03-30T00:00:00+00:00', ?, NULL, NULL
                )
                """,
                ((datetime.now(UTC) - timedelta(seconds=120)).isoformat(),),
            )
            conn.commit()
            return conn

    monkeypatch.setattr(api_server, "JOB_QUEUE", QueueWithUnknownTTL())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = api_server._snapshot_job_subsystem()

    assert snapshot["current_unknown_ttl_jobs"] == 2
    assert snapshot["legacy_unknown_ttl_jobs"] == 0
    assert snapshot["legacy_unknown_ttl_seconds"] == 3600


def test_snapshot_job_subsystem_counts_naive_unknown_ttl_jobs(monkeypatch):
    class QueueWithNaiveUnknownTTL:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 1

        def _connect(self):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    webhook_url TEXT,
                    ttl_seconds INTEGER,
                    legacy_expires_at TEXT
                )
                """)
            conn.execute("""
                INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
                VALUES (
                    'legacy', 'completed', '2026-03-30 00:00:00',
                    '2026-03-30 00:01:00', NULL, '2999-01-01 00:00:00'
                )
                """)
            conn.commit()
            return conn

    monkeypatch.setattr(api_server, "JOB_QUEUE", QueueWithNaiveUnknownTTL())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = api_server._snapshot_job_subsystem()

    assert snapshot["current_unknown_ttl_jobs"] == 1


def test_snapshot_job_subsystem_excludes_invisible_unknown_ttl_jobs(monkeypatch):
    class QueueWithInvisibleUnknownTTL:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

        def _connect(self):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    webhook_url TEXT,
                    ttl_seconds INTEGER,
                    legacy_expires_at TEXT
                )
                """)
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
                VALUES ('legacy', 'completed', '2026-03-30T00:00:00+00:00', ?, NULL, NULL)
                """,
                (
                    (
                        datetime.now(UTC) - timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS + 10)
                    ).isoformat(),
                ),
            )
            conn.commit()
            return conn

    monkeypatch.setattr(api_server, "JOB_QUEUE", QueueWithInvisibleUnknownTTL())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = api_server._snapshot_job_subsystem()

    assert snapshot["current_unknown_ttl_jobs"] == 0


def test_snapshot_job_subsystem_counts_unknown_ttl_with_corrupt_completed_at(
    monkeypatch,
):
    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

    class LegacyQueue:
        state = "open"
        db_path = "legacy.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

        def _connect(self):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    webhook_url TEXT,
                    ttl_seconds INTEGER,
                    legacy_expires_at TEXT
                )
                """)
            conn.execute("""
                INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
                VALUES (
                    'legacy', 'completed', '2026-03-30T00:00:00+00:00',
                    'not-a-date', NULL, '2999-01-01T00:00:00+00:00'
                )
                """)
            conn.commit()
            return conn

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = api_server._snapshot_job_subsystem()

    assert snapshot["status"] == "healthy"
    assert snapshot["legacy_visible_queues"] == 1
    assert snapshot["legacy_db_paths"] == ["legacy.sqlite3"]
    assert snapshot["legacy_unknown_ttl_jobs"] == 1


def test_snapshot_job_subsystem_keeps_legacy_queue_with_missing_completed_at(
    monkeypatch,
):
    class LegacyQueue:
        state = "open"
        db_path = "legacy.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

        def _connect(self):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    webhook_url TEXT,
                    ttl_seconds INTEGER,
                    legacy_expires_at TEXT
                )
                """)
            conn.execute("""
                INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
                VALUES (
                    'legacy', 'completed', '2026-03-30T00:00:00+00:00',
                    NULL, NULL, '2999-01-01T00:00:00+00:00'
                )
                """)
            conn.commit()
            return conn

    monkeypatch.setattr(api_server, "JOB_QUEUE", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = api_server._snapshot_job_subsystem(start_repair=False)

    assert snapshot["legacy_visible_queues"] == 1
    assert snapshot["legacy_db_paths"] == ["legacy.sqlite3"]
    assert snapshot["legacy_unknown_ttl_jobs"] == 1


def test_snapshot_job_subsystem_excludes_unknown_ttl_with_corrupt_completed_at(
    monkeypatch,
):
    class QueueWithCorruptCompletedAt:
        state = "open"
        db_path = "current.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

        def _connect(self):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    webhook_url TEXT,
                    ttl_seconds INTEGER,
                    legacy_expires_at TEXT
                )
                """)
            conn.execute("""
                INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
                VALUES (
                    'legacy', 'completed', '2026-03-30T00:00:00+00:00',
                    'not-a-date', NULL, NULL
                )
                """)
            conn.commit()
            return conn

    monkeypatch.setattr(api_server, "JOB_QUEUE", QueueWithCorruptCompletedAt())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    snapshot = api_server._snapshot_job_subsystem()

    assert snapshot["current_unknown_ttl_jobs"] == 0


def test_remember_job_queue_deduplicates_same_db_path(monkeypatch):
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])

    class Queue:
        state = "open"

        def __init__(self, db_path):
            self.db_path = db_path

    first = Queue("one.sqlite3")
    second = Queue("two.sqlite3")
    third_same_path = Queue("one.sqlite3")
    fourth = Queue("three.sqlite3")

    api_server._remember_job_queue(first)
    api_server._remember_job_queue(second)
    api_server._remember_job_queue(third_same_path)
    api_server._remember_job_queue(fourth)

    history = api_server._JOB_QUEUE_HISTORY
    assert len(history) == 3
    assert history[0] is second
    assert history[1] is third_same_path
    assert history[2] is fourth


def test_remember_job_queue_does_not_drop_visible_queues_by_fixed_history_cap(monkeypatch):
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])

    class Queue:
        state = "open"

        def __init__(self, db_path):
            self.db_path = db_path

    queues = [Queue(f"queue-{index}.sqlite3") for index in range(12)]
    for queue in queues:
        api_server._remember_job_queue(queue)

    assert api_server._JOB_QUEUE_HISTORY == queues


def test_prune_job_queue_history_drops_expired_sqlite_queues(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])

    expired_queue = api_server.PersistentJobQueue(
        str(tmp_path / "expired.sqlite3"), worker_count=1, ttl_seconds=60
    )
    visible_queue = api_server.PersistentJobQueue(
        str(tmp_path / "visible.sqlite3"), worker_count=1, ttl_seconds=60
    )

    with closing(expired_queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, status, created_at, completed_at,
                result_json, error, webhook_url, ttl_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired",
                "completed",
                "2026-03-30T00:00:00+00:00",
                "2026-03-30T00:01:00+00:00",
                "{}",
                None,
                None,
                1,
            ),
        )
        conn.commit()

    with closing(visible_queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, status, created_at, completed_at,
                result_json, error, webhook_url, ttl_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "visible",
                "completed",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
                "{}",
                None,
                None,
                3600,
            ),
        )
        conn.commit()

    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [expired_queue, visible_queue])
    api_server._prune_job_queue_history()

    history = api_server._JOB_QUEUE_HISTORY
    assert expired_queue not in history
    assert visible_queue in history

    expired_queue.close()
    visible_queue.close()


def test_external_owner_backend_read_failure_transitions_queue_to_backend_error(tmp_path):
    queue = api_server._ExternalOwnerJobQueue(tmp_path / "missing.sqlite3")

    with pytest.raises(RuntimeError, match="backend read failed during repair") as exc_info:
        api_server._external_owner_backend_still_owned(queue)

    assert queue.state == "backend_error"
    assert isinstance(exc_info.value.__cause__, sqlite3.Error)


def test_external_owner_backend_still_owned_keeps_fresh_heartbeat_without_pid_metadata(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE queue_leases (
                lease_name TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                owner_pid INTEGER NOT NULL DEFAULT 0,
                owner_start_time REAL
            )
            """)
        conn.execute(
            "INSERT INTO queue_leases "
            "(lease_name, owner_id, heartbeat_at, owner_pid, owner_start_time) "
            "VALUES (?, ?, ?, ?, ?)",
            ("default", "other-owner", datetime.now(UTC).isoformat(), 0, None),
        )
        conn.commit()

    queue = api_server._ExternalOwnerJobQueue(db_path)

    assert api_server._external_owner_backend_still_owned(queue) is True


def test_external_owner_backend_still_owned_keeps_fresh_heartbeat_with_dead_pid(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE queue_leases (
                lease_name TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                owner_pid INTEGER NOT NULL DEFAULT 0,
                owner_start_time REAL
            )
            """)
        conn.execute(
            "INSERT INTO queue_leases "
            "(lease_name, owner_id, heartbeat_at, owner_pid, owner_start_time) "
            "VALUES (?, ?, ?, ?, ?)",
            ("default", "other-owner", datetime.now(UTC).isoformat(), 999999, None),
        )
        conn.commit()

    queue = api_server._ExternalOwnerJobQueue(db_path)

    assert api_server._external_owner_backend_still_owned(queue) is True


def test_external_owner_backend_error_state_restarts_repair(monkeypatch, tmp_path):
    queue = api_server._ExternalOwnerJobQueue(tmp_path / "missing.sqlite3")
    queue.state = "backend_error"

    monkeypatch.setattr(api_server, "JOB_QUEUE", queue)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    api_server._maybe_start_job_queue_repair()

    assert api_server._JOB_QUEUE_REPAIR_THREAD is not None
    api_server._JOB_QUEUE_REPAIR_THREAD.join(timeout=0.1)


def test_prune_job_queue_history_drops_unreadable_legacy_queue_after_threshold(monkeypatch):
    class BrokenQueue:
        state = "open"
        db_path = "broken.sqlite3"

        def _connect(self):
            raise sqlite3.DatabaseError("malformed database")

    queue = BrokenQueue()
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [queue])

    api_server._prune_job_queue_history()
    assert api_server._JOB_QUEUE_HISTORY == [queue]

    api_server._prune_job_queue_history()
    assert api_server._JOB_QUEUE_HISTORY == [queue]

    api_server._prune_job_queue_history()
    assert api_server._JOB_QUEUE_HISTORY == []


def test_prune_job_queue_history_keeps_temporarily_locked_legacy_queue(monkeypatch):
    class LockedQueue:
        state = "open"
        db_path = "locked.sqlite3"

        def _connect(self):
            raise sqlite3.OperationalError("database is locked")

    queue = LockedQueue()
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [queue])

    api_server._prune_job_queue_history()

    assert api_server._JOB_QUEUE_HISTORY == [queue]


def test_start_job_queue_watchdog_keeps_running_if_global_stop_reference_is_cleared(monkeypatch):
    ticks: list[int] = []

    monkeypatch.setattr(api_server, "_JOB_QUEUE_WATCHDOG_THREAD", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_WATCHDOG_STOP", None)
    monkeypatch.setattr(api_server, "_job_queue_watchdog_tick", lambda: ticks.append(1))

    api_server._start_job_queue_watchdog()
    stop_event = api_server._JOB_QUEUE_WATCHDOG_STOP
    thread = api_server._JOB_QUEUE_WATCHDOG_THREAD

    assert stop_event is not None
    assert thread is not None

    monkeypatch.setattr(api_server, "_JOB_QUEUE_WATCHDOG_STOP", None)
    time.sleep(0.6)
    assert ticks

    stop_event.set()
    thread.join(timeout=1.0)


def test_backend_error_repair_loop_converges_without_recreating_replacement_queue(
    monkeypatch, tmp_path
):
    queue = api_server._ExternalOwnerJobQueue(tmp_path / "missing.sqlite3")
    queue.state = "backend_error"

    build_calls = []

    def fake_build_replacement(expected_queue):
        build_calls.append(expected_queue)
        return expected_queue

    monkeypatch.setattr(api_server, "JOB_QUEUE", queue)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "_build_replacement_queue_or_current", fake_build_replacement)
    monkeypatch.setattr(api_server, "_BACKEND_ERROR_REPAIR_RETRY_SECONDS", 0.0)

    api_server._start_job_queue_repair_loop()
    thread = api_server._JOB_QUEUE_REPAIR_THREAD
    assert thread is not None
    thread.join(timeout=1.0)

    assert build_calls == [queue]
    assert api_server._JOB_QUEUE_REPAIR_THREAD is None


def test_init_job_queue_stops_previous_repair_thread(monkeypatch):
    stop_event = threading.Event()
    thread_exited = threading.Event()

    def previous_repair():
        stop_event.wait()
        thread_exited.set()

    previous_thread = threading.Thread(target=previous_repair, daemon=True)
    previous_thread.start()

    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_REPAIR_STOP", stop_event)
    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_REPAIR_THREAD", previous_thread)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    class FreshQueue:
        state = "open"
        db_path = "fresh.sqlite3"

    monkeypatch.setattr(api_server, "_build_job_queue", lambda: FreshQueue())

    resolved = api_server._init_job_queue(None)

    assert resolved.state == "open"
    assert thread_exited.wait(timeout=1.0)


def test_batch_job_status_returns_503_when_external_owner_backend_is_busy(monkeypatch):
    class ExternalOwnerQueue:
        state = "external_owner"
        db_path = "jobs.sqlite3"

        def close(self, *args, **kwargs):
            return None

        def get(self, job_id, cleanup_expired=True):
            raise RuntimeError(
                "Job queue backend is temporarily unavailable because the current owner is busy"
            )

    monkeypatch.setattr(api_server, "JOB_QUEUE", ExternalOwnerQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/api/v1/jobs/some-job")

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]


def test_external_owner_get_preserves_legacy_expires_at(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result_json TEXT,
                error TEXT,
                webhook_url TEXT,
                ttl_seconds INTEGER,
                legacy_expires_at TEXT
            )
            """)
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at,
                result_json, error, webhook_url, ttl_seconds,
                legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                "completed",
                "2026-03-31T00:00:00+00:00",
                "2026-03-31T00:01:00+00:00",
                "{}",
                None,
                None,
                None,
                "2026-03-31T01:00:00+00:00",
            ),
        )
        conn.commit()

    queue = api_server._ExternalOwnerJobQueue(db_path)
    job = queue.get("legacy-job", cleanup_expired=False)

    assert job is not None
    assert job.legacy_expires_at == "2026-03-31T01:00:00+00:00"


def test_external_owner_get_handles_corrupt_result_json(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result_json TEXT,
                error TEXT,
                webhook_url TEXT,
                ttl_seconds INTEGER,
                legacy_expires_at TEXT
            )
            """)
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at,
                result_json, error, webhook_url, ttl_seconds,
                legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                "completed",
                "2026-03-31T00:00:00+00:00",
                "2026-03-31T00:01:00+00:00",
                "not-json",
                None,
                None,
                None,
                "2026-03-31T01:00:00+00:00",
            ),
        )
        conn.commit()

    queue = api_server._ExternalOwnerJobQueue(db_path)
    job = queue.get("legacy-job", cleanup_expired=False)

    assert job is not None
    assert job.result is None


def test_external_owner_get_hides_completed_job_with_corrupt_ttl_seconds(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result_json TEXT,
                error TEXT,
                webhook_url TEXT,
                ttl_seconds INTEGER,
                legacy_expires_at TEXT
            )
            """)
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at,
                result_json, error, webhook_url, ttl_seconds,
                legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "corrupt-ttl-job",
                "completed",
                "2026-03-31T00:00:00+00:00",
                "2026-03-31T00:01:00+00:00",
                "{}",
                None,
                None,
                "not-an-int",
                None,
            ),
        )
        conn.commit()

    queue = api_server._ExternalOwnerJobQueue(db_path)

    assert queue.get("corrupt-ttl-job") is None


def test_batch_job_status_returns_503_when_external_owner_backend_read_fails(monkeypatch):
    class ExternalOwnerQueue:
        state = "external_owner"
        db_path = "jobs.sqlite3"

        def close(self, *args, **kwargs):
            return None

        def get(self, job_id, cleanup_expired=True):
            raise RuntimeError("Job queue backend read failed: malformed database schema")

    monkeypatch.setattr(api_server, "JOB_QUEUE", ExternalOwnerQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/api/v1/jobs/some-job")

    assert response.status_code == 503
    assert "backend read failed" in response.json()["detail"]


def test_batch_job_status_returns_503_when_old_signature_queue_get_raises_sqlite_operational_error(
    monkeypatch,
):
    class OldSignatureQueue:
        state = "open"
        db_path = "jobs.sqlite3"

        def get(self, job_id):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api_server, "JOB_QUEUE", OldSignatureQueue())
    monkeypatch.setattr(api_server, "_job_queue_initialized", True)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/api/v1/jobs/some-job")

    assert response.status_code == 503
    assert "database is locked" in response.json()["detail"]


def test_batch_job_submit_returns_503_when_queue_submit_raises_sqlite_operational_error(
    monkeypatch,
):
    class BadQueue:
        state = "open"
        db_path = "jobs.sqlite3"

        def submit(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api_server, "JOB_QUEUE", BadQueue())
    monkeypatch.setattr(api_server, "_job_queue_initialized", True)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.post(
        "/api/v1/jobs/batch",
        json={
            "urls": ["https://example.com"],
            "mode": "fast",
        },
    )

    assert response.status_code == 503
    assert "database is locked" in response.json()["detail"]


def test_batch_job_status_returns_503_when_job_lookup_raises_sqlite_operational_error(monkeypatch):
    def bad_lookup(job_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api_server, "_get_job_record", bad_lookup)

    response = client.get("/api/v1/jobs/some-job")

    assert response.status_code == 503
    assert "database is locked" in response.json()["detail"]


def test_backend_error_repair_handles_non_runtime_build_failures(monkeypatch, tmp_path):
    queue = api_server._ExternalOwnerJobQueue(tmp_path / "missing.sqlite3")
    queue.state = "backend_error"

    monkeypatch.setattr(api_server, "JOB_QUEUE", queue)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr(api_server, "_BACKEND_ERROR_REPAIR_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(
        api_server,
        "_build_job_queue",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")),
    )

    api_server._start_job_queue_repair_loop()
    thread = api_server._JOB_QUEUE_REPAIR_THREAD
    assert thread is not None
    thread.join(timeout=1.0)

    assert api_server._JOB_QUEUE_REPAIR_THREAD is None
    assert api_server.JOB_QUEUE is queue
    assert queue.state == "backend_error"


def test_stats_reports_unknown_ttl_when_current_queue_is_external_owner(monkeypatch):
    class ExternalOwnerQueue:
        state = "external_owner"
        db_path = "jobs.sqlite3"

        def pending_count(self, cleanup_expired=True):
            return 0

    monkeypatch.setattr(api_server, "JOB_QUEUE", ExternalOwnerQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/api/v1/stats")

    assert response.status_code == 200
    assert response.json()["job_queue"]["ttl_seconds"] is None
    assert response.json()["job_queue"]["max_queued_jobs"] is None


def test_stats_uses_current_queue_capacity_when_available(monkeypatch):
    class Queue:
        state = "open"
        db_path = "jobs.sqlite3"
        ttl_seconds = 120
        max_queued_jobs = 42

        def pending_count(self, cleanup_expired=True):
            return 0

    monkeypatch.setattr(api_server, "JOB_QUEUE", Queue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)

    response = client.get("/api/v1/stats")

    assert response.status_code == 200
    assert response.json()["job_queue"]["max_queued_jobs"] == 42


def test_close_queue_for_repair_ignores_external_owner_wrapper_without_close():
    class ExternalOwnerQueue:
        state = "external_owner"

    api_server._close_queue_for_repair(ExternalOwnerQueue())


def test_health_endpoint_degrades_when_pending_count_raises_sqlite_operational_error(monkeypatch):
    class Queue:
        state = "open"
        db_path = "jobs.sqlite3"

        def pending_count(self, cleanup_expired=True):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api_server, "JOB_QUEUE", Queue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "OPTIONAL_API_KEY", None)

    response = client.get("/api/v1/health/detailed")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["job_queue"]["current_pending"] is None
    assert data["job_queue"]["pending_visible_total"] is None


def test_health_endpoint_degrades_when_old_signature_pending_count_raises_sqlite_operational_error(
    monkeypatch,
):
    class Queue:
        state = "open"
        db_path = "jobs.sqlite3"

        def pending_count(self):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api_server, "JOB_QUEUE", Queue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [])
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "OPTIONAL_API_KEY", None)

    response = client.get("/api/v1/health/detailed")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["job_queue"]["current_pending"] is None
    assert data["job_queue"]["pending_visible_total"] is None


def test_init_job_queue_raises_if_previous_repair_thread_does_not_stop(monkeypatch):
    stop_event = threading.Event()

    def blocked_repair():
        time.sleep(2.0)

    previous_thread = threading.Thread(target=blocked_repair, daemon=True)
    previous_thread.start()

    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_REPAIR_STOP", stop_event)
    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_REPAIR_THREAD", previous_thread)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_WATCHDOG_STOP", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_WATCHDOG_THREAD", None)

    with pytest.raises(
        RuntimeError, match="Previous job queue repair thread did not stop before reload"
    ):
        api_server._init_job_queue(None)


def test_init_job_queue_raises_if_previous_watchdog_thread_does_not_stop(monkeypatch):
    stop_event = threading.Event()

    def blocked_watchdog():
        time.sleep(2.0)

    previous_thread = threading.Thread(target=blocked_watchdog, daemon=True)
    previous_thread.start()

    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_STOP", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_REPAIR_THREAD", None)
    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_WATCHDOG_STOP", stop_event)
    monkeypatch.setattr(api_server, "_PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD", previous_thread)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_WATCHDOG_STOP", None)
    monkeypatch.setattr(api_server, "_JOB_QUEUE_WATCHDOG_THREAD", None)

    with pytest.raises(
        RuntimeError, match="Previous job queue watchdog thread did not stop before reload"
    ):
        api_server._init_job_queue(None)


def test_external_owner_queue_reads_do_not_create_sqlite_file(tmp_path):
    db_path = tmp_path / "missing.sqlite3"
    queue = api_server._ExternalOwnerJobQueue(db_path)

    with pytest.raises(RuntimeError, match="backend read failed"):
        queue.pending_count()

    assert db_path.exists() is False


def test_prune_job_queue_history_drops_legacy_queue_with_invalid_legacy_expires_at(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "legacy-invalid.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result_json TEXT,
                error TEXT,
                webhook_url TEXT,
                ttl_seconds INTEGER,
                legacy_expires_at TEXT
            )
            """)
        conn.execute(
            """
            INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                "completed",
                "2026-03-30T00:00:00+00:00",
                "2026-03-30T00:01:00+00:00",
                None,
                "not-a-date",
            ),
        )
        conn.commit()

    queue = api_server._ExternalOwnerJobQueue(db_path)
    queue.state = "closed"
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [queue])

    api_server._prune_job_queue_history()

    assert api_server._JOB_QUEUE_HISTORY == []


def test_prune_job_queue_history_drops_legacy_queue_with_corrupt_ttl_seconds(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-corrupt-ttl.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result_json TEXT,
                error TEXT,
                webhook_url TEXT,
                ttl_seconds INTEGER,
                legacy_expires_at TEXT
            )
            """)
        conn.execute(
            """
            INSERT INTO jobs (
                    job_id, status, created_at, completed_at,
                    ttl_seconds, legacy_expires_at
                )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                "completed",
                "2026-03-30T00:00:00+00:00",
                (datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
                "not-an-int",
                None,
            ),
        )
        conn.commit()

    queue = api_server._ExternalOwnerJobQueue(db_path)
    queue.state = "closed"
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [queue])

    api_server._prune_job_queue_history()

    assert api_server._JOB_QUEUE_HISTORY == []


def test_get_job_record_uses_job_specific_ttl_instead_of_current_api_ttl(monkeypatch):
    class LegacyQueue:
        db_path = "legacy.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "started_at": None,
                    "completed_at": (datetime.now(UTC) - timedelta(seconds=90)).isoformat(),
                    "result": {"ok": True},
                    "error": None,
                    "ttl_seconds": 120,
                },
            )()

    class CurrentQueue:
        state = "open"
        db_path = "current.sqlite3"

        def get(self, job_id, cleanup_expired=True):
            assert cleanup_expired is False
            return None

    monkeypatch.setattr(api_server, "JOB_QUEUE", CurrentQueue())
    monkeypatch.setattr(api_server, "_JOB_QUEUE_HISTORY", [LegacyQueue()])
    monkeypatch.setattr(api_server, "JOB_TTL_SECONDS", 60)

    job = _get_job_record("legacy-job")

    assert job is not None
    assert job.job_id == "legacy-job"
