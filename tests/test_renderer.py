"""Tests for Playwright render mode."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markdown_ingress import ingest
from markdown_ingress.core.renderer import Renderer

# Skip all tests in this file if playwright is not available
pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def local_test_server():
    """Local HTTP test server to avoid external network dependencies."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self._send_html("<html><body><h1>Example Domain</h1><p>Hello</p></body></html>")
                return

            if self.path == "/js":
                self._send_html("""
                    <html><body>
                    <div id="status">before</div>
                    <script>document.getElementById("status").textContent="js-executed";</script>
                    </body></html>
                    """)
                return

            if self.path == "/delay":
                time.sleep(3.0)
                self._send_html("<html><body><p>slow</p></body></html>")
                return

            if self.path == "/headers":
                payload = {"user-agent": self.headers.get("user-agent", "")}
                self._send_html(f"<html><body>{json.dumps(payload)}</body></html>")
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

        def _send_html(self, body):
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_renderer_basic(local_test_server):
    """Test basic Playwright rendering."""
    renderer = Renderer(timeout=10.0)
    result = await renderer.render(f"{local_test_server}/")

    assert result.status_code == 200
    assert len(result.html) > 0
    assert "Example Domain" in result.html


def test_renderer_sync(local_test_server):
    """Test synchronous renderer wrapper."""
    renderer = Renderer(timeout=10.0)
    result = renderer.render_sync(f"{local_test_server}/")

    assert result.status_code == 200
    assert len(result.html) > 0
    assert "Example Domain" in result.html


def test_ingest_render_mode(local_test_server):
    """Test ingest() with render mode."""
    doc = ingest(f"{local_test_server}/", mode="render", timeout=15.0)

    assert doc.markdown
    assert doc.token_estimate > 0
    assert doc.content_hash.startswith("sha256:")
    assert 0.0 <= doc.injection_score <= 1.0
    assert doc.metadata["mode"] == "render"


def test_render_mode_vs_fast_mode(local_test_server):
    """Compare render mode vs fast mode on same URL."""
    doc_fast = ingest(f"{local_test_server}/", mode="fast", timeout=10.0)
    doc_render = ingest(f"{local_test_server}/", mode="render", timeout=15.0)

    assert len(doc_fast.markdown) > 0
    assert len(doc_render.markdown) > 0
    assert doc_fast.content_hash.startswith("sha256:")
    assert doc_render.content_hash.startswith("sha256:")
    assert doc_fast.injection_score < 0.3
    assert doc_render.injection_score < 0.3


def test_render_mode_timeout(local_test_server):
    """Test that render mode respects timeout."""
    start = time.time()

    with pytest.raises(Exception) as exc:
        ingest(f"{local_test_server}/delay", mode="render", timeout=1.0)

    elapsed = time.time() - start
    assert elapsed < 5.0
    assert "timeout" in str(exc.value).lower() or "Timeout" in str(type(exc.value).__name__)


def test_render_mode_user_agent(local_test_server):
    """Test custom user agent in render mode."""
    renderer = Renderer(timeout=10.0, user_agent="CustomBot/1.0")
    result = renderer.render_sync(f"{local_test_server}/headers")
    assert "CustomBot/1.0" in result.html


@pytest.mark.asyncio
async def test_render_mode_javascript_execution(local_test_server):
    """Test that JavaScript is executed in render mode."""
    renderer = Renderer(timeout=10.0)
    result = await renderer.render(f"{local_test_server}/js")

    assert len(result.html) > 100
    assert result.status_code == 200
    assert "js-executed" in result.html
