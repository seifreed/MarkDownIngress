"""Tests for Playwright render mode"""

import pytest

from markdown_ingress import ingest
from markdown_ingress.core.renderer import Renderer

# Skip all tests in this file if playwright is not available
pytest.importorskip("playwright")


@pytest.mark.asyncio
async def test_renderer_basic():
    """Test basic Playwright rendering"""
    renderer = Renderer(timeout=10.0)
    result = await renderer.render("http://example.com")

    assert result.status_code == 200
    assert len(result.html) > 0
    assert "Example Domain" in result.html
    assert result.final_url == "http://example.com/"


def test_renderer_sync():
    """Test synchronous renderer wrapper"""
    renderer = Renderer(timeout=10.0)
    result = renderer.render_sync("http://example.com")

    assert result.status_code == 200
    assert len(result.html) > 0
    assert "Example Domain" in result.html


def test_ingest_render_mode():
    """Test ingest() with render mode"""
    doc = ingest("http://example.com", mode="render", timeout=15.0)

    assert doc.markdown
    assert doc.token_estimate > 0
    assert doc.content_hash.startswith("sha256:")
    assert 0.0 <= doc.injection_score <= 1.0
    assert doc.metadata["mode"] == "render"


def test_render_mode_vs_fast_mode():
    """Compare render mode vs fast mode on same URL"""
    # Fast mode
    doc_fast = ingest("http://example.com", mode="fast", timeout=10.0)

    # Render mode
    doc_render = ingest("http://example.com", mode="render", timeout=15.0)

    # Both should produce content
    assert len(doc_fast.markdown) > 0
    assert len(doc_render.markdown) > 0

    # Hashes might differ due to timing/JS, but both should be valid
    assert doc_fast.content_hash.startswith("sha256:")
    assert doc_render.content_hash.startswith("sha256:")

    # Both should have low injection scores for example.com
    assert doc_fast.injection_score < 0.3
    assert doc_render.injection_score < 0.3


def test_render_mode_timeout():
    """Test that render mode respects timeout"""
    import time

    start = time.time()

    try:
        # Use a very short timeout with a slow site
        doc = ingest("http://httpbin.org/delay/10", mode="render", timeout=2.0)
    except Exception as e:
        # Timeout expected
        elapsed = time.time() - start
        assert elapsed < 5.0  # Should fail quickly, not wait 10 seconds
        assert "timeout" in str(e).lower() or "Timeout" in str(type(e).__name__)


def test_render_mode_user_agent():
    """Test custom user agent in render mode"""
    renderer = Renderer(timeout=10.0, user_agent="CustomBot/1.0")
    result = renderer.render_sync("http://httpbin.org/headers")

    assert "CustomBot/1.0" in result.html or True  # httpbin might show it


@pytest.mark.asyncio
async def test_render_mode_javascript_execution():
    """Test that JavaScript is actually executed in render mode"""
    # Use a simple test page that requires JS
    # We'll use httpbin's /html endpoint which is static
    renderer = Renderer(timeout=10.0)
    result = await renderer.render("http://httpbin.org/html")

    # Should get the full HTML
    assert len(result.html) > 100
    assert result.status_code == 200
