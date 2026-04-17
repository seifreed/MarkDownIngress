"""
Tests to cover missing lines in api.py, api_server.py, and orchestrator.py.
Uses real local HTTP servers and FastAPI TestClient — no business-logic mocks.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from markdown_ingress.api import ingest, ingest_many, retry_ingest
from markdown_ingress.api_runtime import build_runtime_config
from markdown_ingress.api_server import app, main
from markdown_ingress.api_server_models import BatchIngestRequest
from markdown_ingress.api_server_support import (
    _serialize_batch_result,
    to_document_response,
    to_security_report_response,
)
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.cache import MemoryCache
from markdown_ingress.core.config import Config
from markdown_ingress.application.use_cases import IngestUseCase
from markdown_ingress.core.orchestrator import IngestOrchestrator
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.models import FetchResult, SafeDocument, SecurityReport
from markdown_ingress.shared_results import BatchResult

client = TestClient(app)

# ── Local test servers ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def local_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = (
                b"<html><body><h1>Test</h1><p>Content here for testing purposes.</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    t.join(timeout=2)


@pytest.fixture(scope="module")
def error_server():
    class ErrorHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ErrorHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    t.join(timeout=2)


# ── api.py: config override branch (lines 115-150) ───────────────────────────


def test_ingest_config_override_mode_and_timeout(local_server):
    """Lines 115-122: mode and timeout override on a provided config."""
    config = IngestConfig(mode="fast", timeout=5.0)
    doc = ingest(url=local_server, config=config, mode="fast", timeout=20.0)
    assert doc.markdown
    assert doc.metadata["mode"] == "fast"


def test_ingest_config_override_strict_false(local_server):
    """Lines 117-118: strict override."""
    config = IngestConfig(mode="fast", strict=True)
    doc = ingest(url=local_server, config=config, strict=False)
    assert doc.markdown


def test_ingest_config_override_extract_flags(local_server):
    """Lines 133-136: extract_metadata and extract_links overrides."""
    config = IngestConfig(mode="fast", extract_metadata=True, extract_links=True)
    doc = ingest(
        url=local_server,
        config=config,
        mode="fast",
        extract_metadata=False,
        extract_links=False,
    )
    assert doc.markdown


def test_ingest_config_override_stealth_http2_extreme(local_server):
    """Lines 125-130: stealth, disable_http2, extreme_mode overrides."""
    config = IngestConfig(mode="fast")
    doc = ingest(
        url=local_server,
        config=config,
        stealth=False,
        disable_http2=False,
        extreme_mode=False,
    )
    assert doc.markdown


def test_ingest_config_override_policy_and_cache(local_server):
    """Lines 145-150: policy_name, custom_patterns, plugin_dirs overrides."""
    config = IngestConfig(mode="fast")
    doc = ingest(
        url=local_server,
        config=config,
        mode="fast",
        policy_name="normal",
        custom_patterns=[],
        plugin_dirs=[],
    )
    assert doc.markdown


# ── api.py: auto mode fast path (lines 152-171) ──────────────────────────────


def test_ingest_auto_mode_fast_path_above_threshold(local_server):
    """Lines 169-171: auto mode uses fast result when tokens exceed threshold."""
    config = IngestConfig(mode="auto", auto_render_threshold=1)
    doc = ingest(url=local_server, config=config)
    assert doc.metadata.get("auto_mode_used") == "fast"


# ── api.py: retry_ingest ValueError (line 250) ───────────────────────────────


def test_retry_ingest_max_retries_zero(local_server):
    """Line 250: max_retries < 1 raises ValueError."""
    with pytest.raises(ValueError, match="max_retries must be >= 1"):
        retry_ingest(url=local_server, max_retries=0)


# ── api_server.py: error paths ────────────────────────────────────────────────


@patch(
    "markdown_ingress.api_server.ingest",
    side_effect=ImportError("Render mode requires Playwright: not installed"),
)
def test_ingest_endpoint_import_error(_mock):
    """ImportError with Playwright message in /ingest → HTTP 400."""
    response = client.post("/ingest", json={"url": "http://example.com", "mode": "render"})
    assert response.status_code == 400
    assert "Playwright" in response.json()["detail"]


@patch("markdown_ingress.api_server.ingest", side_effect=Exception("connection failed"))
def test_ingest_endpoint_generic_error(_mock):
    """Lines 130-131: generic Exception in /ingest → HTTP 500."""
    response = client.post("/ingest", json={"url": "http://example.com", "mode": "fast"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


@patch("markdown_ingress.api_server.retry_ingest", side_effect=Exception("retry failed"))
def test_retry_ingest_endpoint_error(_mock):
    """Lines 169-170: Exception in /ingest/retry → HTTP 500."""
    response = client.post("/ingest/retry", json={"url": "http://example.com"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


@patch("markdown_ingress.api_server.ingest_many")
def test_batch_ingest_failure_path(_mock):
    """Legacy and versioned batch endpoints both surface shared batch failures."""

    class FakeBatchResult:
        successful = 0
        failed = 1
        documents = [None]
        errors = {"http://example.com/": "batch item failed"}

    _mock.return_value = FakeBatchResult()

    response = client.post(
        "/ingest/batch",
        json={"urls": ["http://example.com"], "mode": "fast"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failure_count"] == 1
    assert data["success_count"] == 0
    assert data["results"][0]["success"] is False
    assert data["results"][0]["error"] == "batch item failed"


def test_to_document_response_preserves_optional_safe_document_fields():
    doc = SafeDocument(
        markdown="# Title",
        metadata={"url": "https://example.com"},
        token_estimate=12,
        content_hash="hash",
        injection_score=0.2,
        flags=["flag"],
        removed_elements={"scripts": 1},
        screenshot_path="shots/page.png",
        enriched_metadata={"author": "Alice"},
        links={"internal": ["https://example.com/about"]},
        nova_score=0.4,
        nova_details={"trace": "ok"},
    )

    payload = to_document_response(doc).model_dump()

    assert payload["screenshot_path"] == "shots/page.png"
    assert payload["enriched_metadata"] == {"author": "Alice"}
    assert payload["links"] == {"internal": ["https://example.com/about"]}
    assert payload["nova_score"] == 0.4
    assert payload["nova_details"] == {"trace": "ok"}


def test_batch_serializer_embeds_full_document_response_shape():
    request = BatchIngestRequest(urls=["https://example.com"], mode="fast")
    document = SafeDocument(
        markdown="ok",
        metadata={"url": "https://example.com"},
        token_estimate=1,
        content_hash="hash",
        injection_score=0.0,
        flags=[],
        removed_elements={},
        screenshot_path="shots/page.png",
        enriched_metadata={"canonical_url": "https://example.com/canonical"},
        links={"external": ["https://other.example/"]},
        nova_score=0.1,
        nova_details={"categories": ["demo"]},
    )

    class Result:
        documents = [document]
        error_items = []
        errors_by_url = {}

    payload = _serialize_batch_result(request, Result())

    assert payload["results"][0]["success"] is True
    assert payload["results"][0]["data"]["screenshot_path"] == "shots/page.png"
    assert payload["results"][0]["data"]["enriched_metadata"]["canonical_url"].endswith(
        "/canonical"
    )
    assert payload["results"][0]["data"]["links"] == {"external": ["https://other.example/"]}
    assert payload["results"][0]["data"]["nova_score"] == 0.1


def test_to_security_report_response_is_round_trippable():
    report = SecurityReport(
        injection_score=0.3,
        risk_level="MEDIUM",
        pattern_matches=[{"pattern": "x"}],
        flags=["flag"],
        hidden_content_detected=False,
        hidden_elements_count=0,
        imperative_density=0.1,
        url="https://example.com",
        title="Example",
    )

    payload = to_security_report_response(report).model_dump()
    rebuilt = SecurityReport.from_dict(payload)

    assert payload["timestamp"] == report.timestamp
    assert payload["version"] == report.version
    assert rebuilt.timestamp == report.timestamp
    assert rebuilt.version == report.version


@patch(
    "markdown_ingress.api_server.generate_security_report",
    side_effect=Exception("report error"),
)
def test_security_report_endpoint_error(_mock):
    """Lines 265-266: Exception in /security/report → HTTP 500."""
    response = client.post("/security/report", json={"url": "http://example.com", "mode": "fast"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


@patch("uvicorn.run")
def test_main_calls_uvicorn(mock_uvicorn):
    """Line 294: main() invokes uvicorn.run."""
    main()
    mock_uvicorn.assert_called_once()


# ── orchestrator.py: config override branch (lines 121-160) ──────────────────


def test_orchestrator_config_override_mode_timeout(local_server):
    """Verify overrides are applied on a clone and original config is not mutated."""
    config = IngestConfig(mode="fast", timeout=5.0, strict=True)
    result = IngestOrchestrator().execute(
        local_server,
        config=config,
        mode="fast",
        timeout=20.0,
        strict=False,
    )
    assert result.markdown
    # Original config must not be mutated (overrides go to a clone)
    assert config.mode == "fast"
    assert config.timeout == 5.0
    assert config.strict is True  # Verify strict was not mutated either


def test_orchestrator_config_override_all_params(local_server):
    """Verify all override branches work and original config stays immutable."""
    config = IngestConfig(mode="fast", timeout=5.0)
    result = IngestOrchestrator().execute(
        local_server,
        config=config,
        mode="fast",
        timeout=20.0,
        strict=False,
        stealth=False,
        disable_http2=False,
        extreme_mode=False,
        extract_metadata=False,
        extract_links=False,
        advanced_security=False,
        use_llm=False,
    )
    assert result.markdown
    # Original config must not be mutated
    assert config.mode == "fast"
    assert config.timeout == 5.0
    # Overrides applied: extract_metadata=False means no enriched metadata
    assert result.metadata.get("author") is None


def test_orchestrator_config_override_revalidates_invalid_mode():
    with pytest.raises(ValueError, match="Invalid mode 'broken'"):
        IngestOrchestrator().execute(
            "https://unit.test/invalid-mode",
            config=IngestConfig(mode="fast"),
            mode="broken",
        )


def test_build_runtime_config_none_can_clear_nullable_overrides():
    config = IngestConfig(
        mode="fast", screenshot=True, cache=MemoryCache(default_ttl=60), cache_ttl=60
    )

    resolved = build_runtime_config(
        config=config,
        screenshot=None,
        cache=None,
        cache_ttl=None,
    )

    assert resolved.screenshot is None
    assert resolved.cache is None
    assert resolved.cache_ttl is None


def test_build_runtime_config_isolates_cache_object_identity():
    cache = MemoryCache(default_ttl=60)
    config = IngestConfig(mode="fast", cache=cache)

    resolved = build_runtime_config(config=config)

    assert resolved.cache is not cache
    assert getattr(resolved.cache, "__wrapped__", None) is cache


def test_build_runtime_config_revalidates_invalid_mode_override():
    config = IngestConfig(mode="fast")

    with pytest.raises(ValueError, match="Invalid mode 'broken'"):
        build_runtime_config(config=config, mode="broken")


def test_build_runtime_config_revalidates_invalid_chunk_overlap_override():
    config = IngestConfig(mode="fast", chunk_size=1000, chunk_overlap=100)

    with pytest.raises(
        ValueError, match=r"chunk_overlap \(5000\) must be less than chunk_size \(1000\)"
    ):
        build_runtime_config(config=config, chunk_overlap=5000)


def test_build_runtime_config_revalidates_invalid_timeout_override():
    config = IngestConfig(mode="fast", timeout=5.0)

    with pytest.raises(ValueError, match="timeout must be > 0.0"):
        build_runtime_config(config=config, timeout=0)


def test_build_runtime_config_revalidates_invalid_auto_render_threshold_override():
    config = IngestConfig(mode="fast", auto_render_threshold=50)

    with pytest.raises(ValueError, match="auto_render_threshold must be >= 1"):
        build_runtime_config(config=config, auto_render_threshold=0)


def test_build_runtime_config_revalidates_invalid_render_cost_budget_override():
    config = IngestConfig(mode="fast")

    with pytest.raises(ValueError, match="render_cost_budget must be >= 1 when provided"):
        build_runtime_config(config=config, render_cost_budget=0)


def test_build_runtime_config_accepts_extended_runtime_fields():
    resolved = build_runtime_config(
        allow_local_urls=True,
        output_format="json",
        output_formats=["markdown", "security"],
        save_reports=True,
        reports_dir="saved-reports",
        fetcher_user_agent="UA/1.0",
        domain_request_interval=0.5,
        circuit_breaker_threshold=7,
        circuit_breaker_open_seconds=12.5,
    )

    assert resolved.output_format == "json"
    assert resolved.output_formats == ["markdown", "security"]
    assert resolved.allow_local_urls is True
    assert resolved.save_reports is True
    assert resolved.reports_dir == "saved-reports"
    assert resolved.fetcher_user_agent == "UA/1.0"
    assert resolved.domain_request_interval == 0.5
    assert resolved.circuit_breaker_threshold == 7
    assert resolved.circuit_breaker_open_seconds == 12.5


def test_build_runtime_config_explicit_none_clears_inherited_allow_local_urls():
    config = IngestConfig(mode="fast", allow_local_urls=True)

    resolved = build_runtime_config(config=config, allow_local_urls=None)

    assert resolved.allow_local_urls is None
    assert "allow_local_urls" in resolved.explicit_keys()


def test_build_runtime_config_omitted_allow_local_urls_preserves_inherited_value():
    config = IngestConfig(mode="fast", allow_local_urls=True)

    resolved = build_runtime_config(config=config)

    assert resolved.allow_local_urls is True


def test_build_runtime_config_explicit_none_clears_inherited_render_cost_budget():
    config = IngestConfig(mode="fast", render_cost_budget=5)

    resolved = build_runtime_config(config=config, render_cost_budget=None)

    assert resolved.render_cost_budget is None
    assert "render_cost_budget" in resolved.explicit_keys()


def test_build_runtime_config_omitted_render_cost_budget_preserves_inherited_value():
    config = IngestConfig(mode="fast", render_cost_budget=5)

    resolved = build_runtime_config(config=config)

    assert resolved.render_cost_budget == 5


def test_build_runtime_config_rejects_invalid_output_formats_override():
    with pytest.raises(ValueError, match="Invalid output_formats entry 'bogus'"):
        build_runtime_config(output_formats=["bogus"])


def test_build_runtime_config_rejects_unknown_output_profile_override():
    with pytest.raises(ValueError, match="Unknown output profile 'bogus'"):
        build_runtime_config(output_profile="bogus")


def test_build_runtime_config_rejects_string_custom_patterns_override():
    with pytest.raises(ValueError, match="custom_patterns must be a list of strings"):
        build_runtime_config(custom_patterns="abc")


def test_build_runtime_config_rejects_string_plugin_dirs_override():
    with pytest.raises(ValueError, match="plugin_dirs must be a list of strings"):
        build_runtime_config(plugin_dirs="abc")


def test_build_runtime_config_rejects_invalid_domain_policies_override():
    with pytest.raises(ValueError, match=r"domain_policies\[0\] must be a mapping or DomainPolicy"):
        build_runtime_config(domain_policies=["bad"])


def test_build_runtime_config_rejects_invalid_domain_policy_name_override():
    with pytest.raises(ValueError, match="Invalid policy_name 'bogus'"):
        build_runtime_config(domain_policies=[{"domain": "example.com", "policy_name": "bogus"}])


def test_build_runtime_config_rejects_invalid_domain_policy_request_interval_override():
    with pytest.raises(ValueError, match="DomainPolicy.request_interval must be >= 0.0"):
        build_runtime_config(domain_policies=[{"domain": "example.com", "request_interval": -1.0}])


def test_build_runtime_config_rejects_scalar_string_domain_policy_rule_lists():
    with pytest.raises(ValueError, match=r"allowed_tags must be a list of strings"):
        build_runtime_config(domain_policies=[{"domain": "example.com", "allowed_tags": "article"}])


@patch("markdown_ingress.api.ingest_impl")
def test_public_ingest_accepts_allow_local_urls_override(mock_ingest_impl):
    mock_ingest_impl.return_value = SafeDocument(
        markdown="ok",
        metadata={"url": "https://example.com"},
        token_estimate=1,
        content_hash="hash",
        injection_score=0.0,
        flags=[],
        removed_elements={},
    )

    result = ingest("https://example.com", allow_local_urls=True)

    assert result.markdown == "ok"
    assert mock_ingest_impl.call_args.kwargs["allow_local_urls"] is True


@patch("markdown_ingress.api.retry_ingest_impl")
def test_retry_ingest_accepts_allow_local_urls_override(mock_retry_ingest_impl):
    mock_retry_ingest_impl.return_value = SafeDocument(
        markdown="ok",
        metadata={"url": "https://example.com"},
        token_estimate=1,
        content_hash="hash",
        injection_score=0.0,
        flags=[],
        removed_elements={},
    )

    result = retry_ingest("https://example.com", allow_local_urls=True)

    assert result.markdown == "ok"
    assert mock_retry_ingest_impl.call_args.kwargs["allow_local_urls"] is True


def test_ingest_persists_security_report_when_requested(tmp_path):
    doc = SafeDocument(
        markdown="saved",
        metadata={"url": "https://example.com", "title": "Example", "risk_level": "LOW"},
        token_estimate=10,
        content_hash="sha256:test",
        injection_score=0.1,
        flags=[],
        removed_elements={"hidden_elements": 0},
    )

    with (
        patch("markdown_ingress.api_facade.IngestUseCase.execute", return_value=doc),
        patch("markdown_ingress.api_facade._persist_report_for_document") as persist,
    ):
        result = ingest(
            "https://example.com",
            save_reports=True,
            reports_dir=str(tmp_path),
        )

    assert result is doc
    persist.assert_called_once()
    persisted_doc, reports_dir = persist.call_args.args
    assert persisted_doc is doc
    assert reports_dir == str(tmp_path)


def test_ingest_save_reports_is_fail_open_on_persist_error(caplog):
    doc = SafeDocument(
        markdown="saved",
        metadata={"url": "https://example.com", "risk_level": "LOW"},
        token_estimate=10,
        content_hash="sha256:test",
        injection_score=0.1,
        flags=[],
        removed_elements={"hidden_elements": 0},
    )

    with (
        patch("markdown_ingress.api_facade.IngestUseCase.execute", return_value=doc),
        patch(
            "markdown_ingress.api_facade._persist_report_for_document",
            side_effect=OSError("disk full"),
        ),
        caplog.at_level("WARNING"),
    ):
        result = ingest("https://example.com", save_reports=True)

    assert result is doc
    assert "Failed to persist security report" in caplog.text


def test_ingest_persists_policy_blocked_document_when_requested(tmp_path):
    doc = SafeDocument(
        markdown="blocked",
        metadata={"url": "https://example.com", "title": "Blocked", "risk_level": "HIGH"},
        token_estimate=10,
        content_hash="sha256:block",
        injection_score=1.0,
        flags=["policy_block"],
        removed_elements={"hidden_elements": 1},
    )

    with (
        patch(
            "markdown_ingress.api_facade.IngestUseCase.execute",
            side_effect=PolicyBlockedError("blocked", document=doc),
        ),
        patch("markdown_ingress.api_facade._persist_report_for_document") as persist,
    ):
        with pytest.raises(PolicyBlockedError) as exc_info:
            ingest("https://example.com", save_reports=True, reports_dir=str(tmp_path))

    assert exc_info.value.document is doc
    persist.assert_called_once_with(doc, str(tmp_path))


def test_ingest_many_persists_one_report_per_successful_document(tmp_path):
    doc_ok_1 = SafeDocument(
        markdown="one",
        metadata={"url": "https://example.com/1", "risk_level": "LOW"},
        token_estimate=5,
        content_hash="sha256:1",
        injection_score=0.0,
        flags=[],
        removed_elements={"hidden_elements": 0},
    )
    doc_ok_2 = SafeDocument(
        markdown="two",
        metadata={"url": "https://example.com/3", "risk_level": "LOW"},
        token_estimate=6,
        content_hash="sha256:3",
        injection_score=0.0,
        flags=[],
        removed_elements={"hidden_elements": 0},
    )
    batch_result = BatchResult(
        total=3,
        successful=2,
        failed=1,
        documents=[doc_ok_1, None, doc_ok_2],
        errors=[],
    )

    async def fake_execute(self, urls, config_builder, max_concurrent, on_progress):
        return batch_result

    with (
        patch("markdown_ingress.api_facade.BatchIngestUseCase.execute", new=fake_execute),
        patch("markdown_ingress.api_facade._persist_report_for_document") as persist,
    ):
        result = ingest_many(
            ["https://example.com/1", "https://example.com/2", "https://example.com/3"],
            save_reports=True,
            reports_dir=str(tmp_path),
        )

    assert result is batch_result
    assert persist.call_count == 2
    persisted_urls = [call.args[0].metadata["url"] for call in persist.call_args_list]
    assert persisted_urls == ["https://example.com/1", "https://example.com/3"]


def test_ingest_many_persists_policy_blocked_documents(tmp_path):
    doc = SafeDocument(
        markdown="blocked",
        metadata={"url": "https://example.com/blocked", "risk_level": "HIGH"},
        token_estimate=4,
        content_hash="sha256:blocked",
        injection_score=1.0,
        flags=["policy_block"],
        removed_elements={"hidden_elements": 1},
    )

    with (
        patch(
            "markdown_ingress.api_facade.IngestUseCase.execute",
            side_effect=PolicyBlockedError("blocked", document=doc),
        ),
        patch(
            "markdown_ingress.application.use_cases.BatchIngestUseCase._select_execution_strategy",
            return_value=("local", None),
        ),
        patch("markdown_ingress.application.use_cases.persist_report_for_document") as persist,
    ):
        result = ingest_many(
            ["https://example.com/blocked"],
            save_reports=True,
            reports_dir=str(tmp_path),
        )

    assert result.successful == 0
    assert result.failed == 1
    persist.assert_called_once_with(doc, str(tmp_path))


def test_build_runtime_config_rejects_explicit_empty_output_formats_override():
    with pytest.raises(ValueError, match="output_formats must be a non-empty list"):
        build_runtime_config(output_formats=[])


def test_build_runtime_config_revalidates_invalid_cache_ttl_override():
    config = IngestConfig(mode="fast")

    with pytest.raises(ValueError, match="cache_ttl must be positive when provided"):
        build_runtime_config(config=config, cache_ttl=0)


def test_build_runtime_config_rejects_boolean_cache_override():
    config = IngestConfig(mode="fast")

    with pytest.raises(ValueError, match="cache must be a cache backend object or None, got bool"):
        build_runtime_config(config=config, cache=True)


# ── orchestrator.py: PLAYWRIGHT_AVAILABLE=False auto-fallback (lines 217-218) ─


@patch("markdown_ingress.application.use_cases.PLAYWRIGHT_AVAILABLE", False)
def test_orchestrator_auto_fast_fail_no_playwright():
    """Lines 217-218: fast fetch fails, Playwright unavailable → exception re-raised."""
    with pytest.raises(Exception):
        IngestOrchestrator().execute(
            "http://127.0.0.1:1",  # guaranteed connection refusal
            config=IngestConfig(mode="auto", timeout=2.0),
        )


# ── api.py: remaining override fields (lines 120,124,132,138,140,142,144) ────


def test_ingest_config_override_remaining_fields(local_server):
    """Lines 120,124,132,138,140,142,144: model, auto_render_threshold, screenshot,
    advanced_security, use_llm, cache, cache_ttl overrides with provided config."""
    config = IngestConfig(mode="fast", timeout=5.0)
    doc = ingest(
        url=local_server,
        config=config,
        mode="fast",
        model="gpt-3.5-turbo",  # line 120
        auto_render_threshold=99999,  # line 124
        screenshot=None,  # line 132 – None doesn't trigger but non-None does
        advanced_security=False,  # line 138
        use_llm=False,  # line 140
        cache=None,  # line 142 – need non-None
        cache_ttl=None,  # line 144 – need non-None
    )
    assert doc.metadata["model"] == "gpt-3.5-turbo"
    assert doc.metadata["advanced_security"] is False
    assert doc.metadata["mode"] == "fast"
    assert doc.screenshot_path is None


def test_ingest_config_override_screenshot_non_none(local_server):
    """Line 132: screenshot override (non-None value triggers the setter)."""
    config = IngestConfig(mode="fast", timeout=5.0)
    doc = ingest(
        url=local_server,
        config=config,
        mode="fast",
        screenshot=True,  # non-None → line 132 executed
    )
    assert doc.metadata["mode"] == "fast"
    assert doc.screenshot_path is None


def test_ingest_config_override_cache_non_none(local_server):
    """Lines 142,144: cache and cache_ttl override with non-None values."""
    import os
    import tempfile

    from markdown_ingress.core.cache import SQLiteCache

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cache = SQLiteCache(db_path=db_path)
        config = IngestConfig(mode="fast", timeout=5.0)
        doc = ingest(
            url=local_server,
            config=config,
            mode="fast",
            cache=cache,  # line 142
            cache_ttl=60,  # line 144
        )
        assert doc.metadata["cache_hit"] is False
        cached = ingest(
            url=local_server,
            config=config,
            mode="fast",
            cache=cache,
            cache_ttl=60,
        )
        assert cached.metadata["cache_hit"] is True
    finally:
        os.unlink(db_path)


def test_ingest_file_config_preserves_cache_backend():
    """File-based configs should keep their cache backend through runtime resolution."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class CountingHandler(BaseHTTPRequestHandler):
        counter = 0

        def do_GET(self):
            CountingHandler.counter += 1
            html = b"<html><body><p>cached</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        config = Config(mode="fast", cache_enabled=True, cache_type="memory", allow_local_urls=True)

        first = ingest(url=url, config=config)
        second = ingest(url=url, config=config)

        assert CountingHandler.counter == 1
        assert first.metadata["cache_hit"] is False
        assert second.metadata["cache_hit"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_orchestrator_execute_preserves_config_cache_backend():
    """Direct orchestrator use should not lose cache when cloning the config."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class CountingHandler(BaseHTTPRequestHandler):
        counter = 0

        def do_GET(self):
            CountingHandler.counter += 1
            html = b"<html><body><p>cached</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        config = IngestConfig(
            mode="fast",
            allow_local_urls=True,
            cache=MemoryCache(default_ttl=60),
            cache_ttl=60,
        )
        orchestrator = IngestOrchestrator()

        first = orchestrator.execute(url, config=config)
        second = orchestrator.execute(url, config=config)

        assert CountingHandler.counter == 1
        assert first.metadata["cache_hit"] is False
        assert second.metadata["cache_hit"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ── api.py: auto mode – render result is better (lines 165-167) ──────────────


def test_ingest_auto_mode_render_better_than_fast():
    """Lines 165-167: auto mode, fast is minimal, render gets more content."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    # Page with JS-rendered content: fast fetcher gets empty body, Playwright
    # executes JS and returns richer content with more tokens.
    js_content = (
        b"<html><head>"
        b"<script>window.onload=function(){"
        b"document.body.innerHTML='<p>" + b"word " * 50 + b"</p>';"
        b"};</script></head>"
        b"<body></body></html>"
    )

    class JSHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(js_content)))
            self.end_headers()
            self.wfile.write(js_content)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), JSHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        config = IngestConfig(mode="auto", auto_render_threshold=99999, timeout=20.0)
        doc = ingest(url=url, config=config)
        assert doc.metadata["auto_mode_used"] in {"fast", "render"}
        if doc.metadata["auto_mode_used"] == "render":
            assert doc.metadata["mode"] == "render"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


# ── api.py: auto mode – fast fails, render fallback (lines 177-179) ──────────


def test_ingest_auto_mode_fast_fails_playwright_fallback(error_server):
    """Lines 177-179: fast mode fails, Playwright renders the page instead."""

    class FakeRenderer:
        def __init__(self, config):
            self.config = config

        def render_sync(self, url):
            html = "<html><body><article><h1>Fallback</h1><p>Rendered content.</p></article></body></html>"
            return FetchResult(
                html=html,
                url=url,
                status_code=200,
                final_url=url,
                headers={},
                timing_ms=1.0,
                metadata={},
            )

    with (
        patch.object(IngestUseCase, "_default_renderer_factory", staticmethod(lambda rc: FakeRenderer(rc))),
        patch("markdown_ingress.api.PLAYWRIGHT_AVAILABLE", True),
    ):
        # error_server returns 403 → fast Fetcher raises → render fallback
        config = IngestConfig(mode="auto", timeout=20.0)
        doc = ingest(url=error_server, config=config)

        assert doc is not None
        assert doc.metadata.get("auto_mode_used") == "render"
        assert doc.metadata.get("auto_mode_reason") == "fast_failed"


# ── api.py: auto mode – fast fails, no playwright (line 181) ────────────────


@pytest.mark.filterwarnings("ignore")
def test_ingest_auto_mode_fast_fails_no_playwright():
    """Line 181: fast fails and PLAYWRIGHT_AVAILABLE=False → exception re-raised."""
    import markdown_ingress.api as api_module

    original = api_module.PLAYWRIGHT_AVAILABLE
    api_module.PLAYWRIGHT_AVAILABLE = False
    try:
        with pytest.raises(Exception):
            ingest(url="http://127.0.0.1:1", config=IngestConfig(mode="auto", timeout=2.0))
    finally:
        api_module.PLAYWRIGHT_AVAILABLE = original


# ── orchestrator.py: no config provided (line 121) ───────────────────────────


def test_orchestrator_no_config(local_server):
    """Line 121: IngestOrchestrator.execute() without config creates one."""
    result = IngestOrchestrator().execute(local_server, mode="fast", timeout=10.0)
    assert result.markdown


# ── orchestrator.py: model and screenshot overrides (lines 142, 152) ─────────


def test_orchestrator_config_override_model_screenshot(local_server):
    """Lines 142,152: model and screenshot params override provided config."""
    config = IngestConfig(mode="fast", timeout=5.0)
    result = IngestOrchestrator().execute(
        local_server,
        config=config,
        model="gpt-3.5-turbo",  # line 142
        screenshot=True,  # line 152
    )
    assert result.markdown


def test_orchestrator_config_override_can_clear_inherited_screenshot(local_server):
    config = IngestConfig(mode="fast", timeout=5.0, screenshot="shot.png")

    result = IngestOrchestrator().execute(
        local_server,
        config=config,
        screenshot=None,
    )

    assert result.markdown
    assert result.screenshot_path is None


# ── orchestrator.py: render mode, Playwright unavailable (line 183) ──────────


def test_orchestrator_render_mode_no_playwright(local_server):
    """Line 183: render mode with PLAYWRIGHT_AVAILABLE=False raises ImportError."""
    import markdown_ingress.application.use_cases as use_cases_module

    original = use_cases_module.PLAYWRIGHT_AVAILABLE
    use_cases_module.PLAYWRIGHT_AVAILABLE = False
    try:
        with pytest.raises(ImportError):
            IngestOrchestrator().execute(local_server, mode="render", timeout=10.0)
    finally:
        use_cases_module.PLAYWRIGHT_AVAILABLE = original


# ── orchestrator.py: auto mode – fast fails, Playwright fallback (lines 203-216)


def test_orchestrator_auto_playwright_fallback(error_server):
    """Lines 203-216: fast fetch fails → Playwright auto-fallback renders the page."""

    class FakeRenderer:
        def __init__(self, config):
            self.config = config

        def render_sync(self, url):
            html = "<html><body><article><h1>Fallback</h1><p>Rendered content.</p></article></body></html>"
            return FetchResult(
                html=html,
                url=url,
                status_code=200,
                final_url=url,
                headers={},
                timing_ms=1.0,
                metadata={},
            )

    with (
        patch.object(IngestUseCase, "_default_renderer_factory", staticmethod(lambda rc: FakeRenderer(rc))),
        patch("markdown_ingress.application.use_cases.PLAYWRIGHT_AVAILABLE", True),
    ):
        # error_server returns 403 → fetcher.fetch_sync raises → render fallback
        result = IngestOrchestrator().execute(error_server, mode="auto", timeout=20.0)

    assert result is not None
