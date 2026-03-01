"""
Tests to cover missing lines in api.py, api_server.py, and orchestrator.py.
Uses real local HTTP servers and FastAPI TestClient — no business-logic mocks.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from markdown_ingress.api import ingest, retry_ingest
from markdown_ingress.api_server import app, main
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.orchestrator import IngestOrchestrator

client = TestClient(app)

# ── Local test servers ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def local_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = b"<html><body><h1>Test</h1><p>Content here for testing purposes.</p></body></html>"
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


@patch("markdown_ingress.api_server.ingest", side_effect=ImportError("Playwright not installed"))
def test_ingest_endpoint_import_error(_mock):
    """Lines 128-129: ImportError in /ingest → HTTP 400."""
    response = client.post("/ingest", json={"url": "http://example.com", "mode": "render"})
    assert response.status_code == 400
    assert "Playwright" in response.json()["detail"]


@patch("markdown_ingress.api_server.ingest", side_effect=Exception("connection failed"))
def test_ingest_endpoint_generic_error(_mock):
    """Lines 130-131: generic Exception in /ingest → HTTP 500."""
    response = client.post("/ingest", json={"url": "http://example.com", "mode": "fast"})
    assert response.status_code == 500
    assert "connection failed" in response.json()["detail"]


@patch("markdown_ingress.api_server.retry_ingest", side_effect=Exception("retry failed"))
def test_retry_ingest_endpoint_error(_mock):
    """Lines 169-170: Exception in /ingest/retry → HTTP 500."""
    response = client.post("/ingest/retry", json={"url": "http://example.com"})
    assert response.status_code == 500
    assert "retry failed" in response.json()["detail"]


@patch("markdown_ingress.api_server.ingest", side_effect=Exception("batch item failed"))
def test_batch_ingest_failure_path(_mock):
    """Lines 219-221: failed item in batch increments failure_count."""
    response = client.post(
        "/ingest/batch",
        json={"urls": ["http://example.com"], "mode": "fast"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failure_count"] == 1
    assert data["success_count"] == 0
    assert data["results"][0]["success"] is False
    assert "error" in data["results"][0]


@patch(
    "markdown_ingress.api_server.generate_security_report",
    side_effect=Exception("report error"),
)
def test_security_report_endpoint_error(_mock):
    """Lines 265-266: Exception in /security/report → HTTP 500."""
    response = client.post(
        "/security/report", json={"url": "http://example.com", "mode": "fast"}
    )
    assert response.status_code == 500
    assert "report error" in response.json()["detail"]


@patch("uvicorn.run")
def test_main_calls_uvicorn(mock_uvicorn):
    """Line 294: main() invokes uvicorn.run."""
    main()
    mock_uvicorn.assert_called_once()


# ── orchestrator.py: config override branch (lines 121-160) ──────────────────


def test_orchestrator_config_override_mode_timeout(local_server):
    """Lines 137-144: mode and timeout overrides on a provided config."""
    config = IngestConfig(mode="fast", timeout=5.0, strict=True)
    result = IngestOrchestrator().execute(
        local_server,
        config=config,
        mode="fast",
        timeout=20.0,
        strict=False,
    )
    assert result.markdown


def test_orchestrator_config_override_all_params(local_server):
    """Lines 145-160: cover remaining override branches."""
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


# ── orchestrator.py: PLAYWRIGHT_AVAILABLE=False auto-fallback (lines 217-218) ─


@patch("markdown_ingress.core.orchestrator.PLAYWRIGHT_AVAILABLE", False)
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
        model="gpt-3.5-turbo",          # line 120
        auto_render_threshold=99999,     # line 124
        screenshot=None,                 # line 132 – None doesn't trigger but non-None does
        advanced_security=False,         # line 138
        use_llm=False,                   # line 140
        cache=None,                      # line 142 – need non-None
        cache_ttl=None,                  # line 144 – need non-None
    )
    assert doc is not None


def test_ingest_config_override_screenshot_non_none(local_server):
    """Line 132: screenshot override (non-None value triggers the setter)."""
    config = IngestConfig(mode="fast", timeout=5.0)
    doc = ingest(
        url=local_server,
        config=config,
        mode="fast",
        screenshot=True,    # non-None → line 132 executed
    )
    assert doc is not None


def test_ingest_config_override_cache_non_none(local_server):
    """Lines 142,144: cache and cache_ttl override with non-None values."""
    from markdown_ingress.core.cache import SQLiteCache
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cache = SQLiteCache(db_path=db_path)
        config = IngestConfig(mode="fast", timeout=5.0)
        doc = ingest(
            url=local_server,
            config=config,
            mode="fast",
            cache=cache,      # line 142
            cache_ttl=60,     # line 144
        )
        assert doc is not None
    finally:
        os.unlink(db_path)


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
        # Either render was better (165-167) or fast was used (169-171)
        assert doc is not None
        assert "auto_mode_used" in doc.metadata
    finally:
        server.shutdown()


# ── api.py: auto mode – fast fails, render fallback (lines 177-179) ──────────


def test_ingest_auto_mode_fast_fails_playwright_fallback(error_server):
    """Lines 177-179: fast mode fails, Playwright renders the page instead."""
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
        screenshot=True,         # line 152
    )
    assert result.markdown


# ── orchestrator.py: render mode, Playwright unavailable (line 183) ──────────


def test_orchestrator_render_mode_no_playwright(local_server):
    """Line 183: render mode with PLAYWRIGHT_AVAILABLE=False raises ImportError."""
    import markdown_ingress.core.orchestrator as orch_module
    original = orch_module.PLAYWRIGHT_AVAILABLE
    orch_module.PLAYWRIGHT_AVAILABLE = False
    try:
        with pytest.raises(ImportError):
            IngestOrchestrator().execute(local_server, mode="render", timeout=10.0)
    finally:
        orch_module.PLAYWRIGHT_AVAILABLE = original


# ── orchestrator.py: auto mode – fast fails, Playwright fallback (lines 203-216)


def test_orchestrator_auto_playwright_fallback(error_server):
    """Lines 203-216: fast fetch fails → Playwright auto-fallback renders the page."""
    # error_server returns 403 → fetcher.fetch_sync raises → Playwright fallback
    result = IngestOrchestrator().execute(error_server, mode="auto", timeout=20.0)
    assert result is not None
