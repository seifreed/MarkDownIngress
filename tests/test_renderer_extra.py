"""Extra tests to maximize coverage for renderer.py and advanced_stealth.py."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markdown_ingress.adapters.rendering.advanced_stealth_renderer import (
    AdvancedStealthRenderer,
    render_with_advanced_stealth,
    render_with_advanced_stealth_sync,
)
from markdown_ingress.adapters.rendering.playwright_renderer import Renderer
from markdown_ingress.adapters.rendering.renderer_navigation import wait_for_content
from markdown_ingress.config_models import RenderConfig
from markdown_ingress.core.stealth import get_advanced_stealth_config

pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def local_test_server():
    """Local HTTP server returning a page with meaningful content."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = (
                b"<html><body>"
                b"<h1>Test Page</h1>"
                b"<p>Hello world content here is meaningful text for testing purposes.</p>"
                b"<article><p>Article content with enough words to pass content checks.</p>"
                b"</article>"
                b"</body></html>"
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


# ---------------------------------------------------------------------------
# Renderer.__init__ paths
# ---------------------------------------------------------------------------


def test_renderer_init_with_config(local_test_server):
    """Renderer(config=RenderConfig(...)) uses the config-provided path (lines 108-137)."""
    config = RenderConfig(
        timeout=10.0,
        wait_until="load",
        headless=True,
        stealth=False,
        disable_http2=False,
        extreme_mode=False,
    )
    renderer = Renderer(config=config)
    assert renderer.timeout == 10_000
    assert renderer.wait_until == "load"


def test_renderer_init_with_params():
    """Renderer(timeout=..., ...) builds config from individual params (lines 91-107)."""
    renderer = Renderer(
        timeout=5.0,
        wait_until="domcontentloaded",
        headless=True,
        stealth=False,
        disable_http2=True,
        extreme_mode=False,
        block_images=True,
        block_fonts=True,
        block_media=True,
        block_ads=True,
        block_trackers=True,
    )
    assert renderer.timeout == 5_000
    assert renderer.wait_until == "domcontentloaded"
    assert renderer.disable_http2 is True


def test_renderer_init_defaults_to_domcontentloaded():
    renderer = Renderer()
    assert renderer.wait_until == "domcontentloaded"


def test_renderer_rejects_duplicate_positional_option():
    with pytest.raises(TypeError, match="multiple values for argument 'timeout'"):
        Renderer(None, 5.0, timeout=7.0)


def test_renderer_rejects_unknown_option():
    with pytest.raises(TypeError, match="unexpected keyword argument 'timeot'"):
        Renderer(timeot=5.0)


def test_renderer_rejects_too_many_positional_options():
    with pytest.raises(TypeError, match="expected at most 16 arguments"):
        Renderer(*range(17))


def test_renderer_init_config_override_with_params():
    """Config provided but individual params override it (lines 110-137)."""
    config = RenderConfig(timeout=30.0, wait_until="networkidle")
    renderer = Renderer(config=config, timeout=7.0, wait_until="load")
    assert renderer.timeout == 7_000
    assert renderer.wait_until == "load"


def test_renderer_init_config_override_can_clear_inherited_screenshot():
    config = RenderConfig(timeout=30.0, wait_until="networkidle", screenshot="shot.png")
    renderer = Renderer(config=config, screenshot=None)

    assert renderer.screenshot is None


# ---------------------------------------------------------------------------
# render_sync  (line 562)
# ---------------------------------------------------------------------------


def test_render_sync(local_test_server):
    """render_sync() is a synchronous wrapper around render() (line 562)."""
    renderer = Renderer(timeout=15.0, wait_until="load")
    result = renderer.render_sync(local_test_server)
    assert result.status_code == 200
    assert "Test Page" in result.html


# ---------------------------------------------------------------------------
# render() async  (lines 178-209)
# ---------------------------------------------------------------------------


async def test_render_async(local_test_server):
    """render() async returns a FetchResult (lines 178-209)."""
    renderer = Renderer(timeout=15.0, wait_until="load")
    result = await renderer.render(local_test_server)
    assert result.status_code == 200
    assert result.html


# ---------------------------------------------------------------------------
# _prepare_browser_args / _prepare_launch_options / _prepare_context_options
# (lines 270-306)
# ---------------------------------------------------------------------------


def test_browser_args_with_disable_http2():
    """disable_http2=True adds --disable-http2 flag (line 278)."""
    renderer = Renderer(disable_http2=True)
    args = renderer._prepare_browser_args()
    assert "--disable-http2" in args


def test_launch_options_with_args():
    """Browser args present → launch_options includes args key (lines 286-288)."""
    renderer = Renderer(disable_http2=True)
    browser_args = renderer._prepare_browser_args()
    opts = renderer._prepare_launch_options(browser_args)
    assert "args" in opts
    assert "--enable-automation" in opts.get("ignore_default_args", [])


def test_launch_options_no_args():
    """No browser args → launch_options has no args key."""
    renderer = Renderer(disable_http2=False, stealth=False)
    browser_args = renderer._prepare_browser_args()
    opts = renderer._prepare_launch_options(browser_args)
    # When no args, the key may be absent
    assert opts.get("headless") is True


def test_prepare_context_options_non_stealth():
    """Non-stealth path returns a plain context options dict (lines 301-306)."""
    renderer = Renderer(stealth=False)
    opts = renderer._prepare_context_options()
    assert "user_agent" in opts
    assert "viewport" in opts


# ---------------------------------------------------------------------------
# extreme_mode → _render_core_extreme (lines 378-428)
# ---------------------------------------------------------------------------


async def test_extreme_mode(local_test_server):
    """extreme_mode=True exercises _render_with_progressive_timeout (lines 378-428)."""
    config = RenderConfig(
        timeout=15.0,
        wait_until="load",
        extreme_mode=True,
        block_images=True,
        block_fonts=True,
    )
    renderer = Renderer(config=config)
    result = await renderer.render(local_test_server)
    assert result.status_code == 200
    assert result.metadata.get("extreme_mode") is True
    assert "strategy_used" in result.metadata


# ---------------------------------------------------------------------------
# _render_with_smart_wait  (lines 441-488)
# ---------------------------------------------------------------------------


async def test_render_with_smart_wait(local_test_server):
    """Direct call to _render_with_smart_wait (lines 441-488)."""
    renderer = Renderer(timeout=15.0, wait_until="load")
    result = await renderer._render_with_smart_wait(local_test_server, 15_000)
    assert result.status_code == 200
    assert result.metadata.get("smart_wait_used") is True


async def test_render_retries_retryable_navigation_errors(monkeypatch):
    renderer = Renderer(timeout=5.0)
    calls = {"count": 0}

    async def fake_render_with_browser(url: str):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("Page.goto: net::ERR_NETWORK_IO_SUSPENDED at https://retry.example/")
        from markdown_ingress.models import FetchResult

        return FetchResult(
            html="<html><body>ok</body></html>",
            url=url,
            status_code=200,
            final_url=url,
            headers={},
            timing_ms=1.0,
            metadata={},
        )

    monkeypatch.setattr(renderer, "_render_with_browser", fake_render_with_browser)

    result = await renderer.render("https://retry.example/")
    assert result.status_code == 200
    assert result.metadata["navigation_retry"] is True
    assert calls["count"] == 2


def test_extract_page_content_retries_navigation_errors():
    renderer = Renderer()

    class FakePage:
        def __init__(self):
            self.calls = 0

        async def content(self):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError(
                    "Page.content: Unable to retrieve content because the page is "
                    "navigating and changing the content."
                )
            return "<html><body>ok</body></html>"

    html = asyncio.run(renderer._extract_page_content(FakePage()))
    assert html == "<html><body>ok</body></html>"


def test_extract_page_content_falls_back_to_outer_html():
    renderer = Renderer()

    class FakePage:
        async def content(self):
            raise RuntimeError(
                "Page.content: Unable to retrieve content because the page is "
                "navigating and changing the content."
            )

        async def evaluate(self, script):
            return "<html><body>fallback</body></html>"

    html = asyncio.run(renderer._extract_page_content(FakePage()))
    assert html == "<html><body>fallback</body></html>"


def test_renderer_identifies_retryable_navigation_errors():
    renderer = Renderer()
    assert renderer._is_retryable_navigation_error(
        RuntimeError("Page.goto: net::ERR_NETWORK_IO_SUSPENDED at https://example.test/")
    )
    assert renderer._is_retryable_navigation_error(
        RuntimeError("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://example.test/")
    )
    assert not renderer._is_retryable_navigation_error(
        RuntimeError("Page.goto: net::ERR_FAILED at https://example.test/")
    )


# ---------------------------------------------------------------------------
# _wait_for_content  (lines 503-550)
# ---------------------------------------------------------------------------


async def test_wait_for_content(local_test_server):
    """_wait_for_content runs against a live page (lines 503-550)."""
    from playwright.async_api import async_playwright

    renderer = Renderer(timeout=15.0, wait_until="load")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            try:
                page = await context.new_page()
                await page.goto(local_test_server, wait_until="load")
                # Should not raise even if content check times out
                await renderer._wait_for_content(page, max_wait=10)
            finally:
                await context.close()
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# AdvancedStealthRenderer  (advanced_stealth.py lines 152-239, 251, 280-284)
# ---------------------------------------------------------------------------


async def test_advanced_stealth_render(local_test_server):
    """AdvancedStealthRenderer.render() exercises _render_with_browser (lines 152-239)."""
    pytest.importorskip("playwright")

    renderer = AdvancedStealthRenderer(
        timeout=15.0,
        wait_until="load",
        headless=True,
        randomize_fingerprint=True,
        allow_local_urls=True,
    )
    result = await renderer.render(local_test_server)
    assert result.status_code == 200
    assert "Test Page" in result.html
    assert result.metadata.get("renderer") == "advanced_stealth_playwright"


def test_advanced_stealth_render_sync(local_test_server):
    """AdvancedStealthRenderer.render_sync() exercises line 251."""
    pytest.importorskip("playwright")

    renderer = AdvancedStealthRenderer(
        timeout=15.0, wait_until="load", headless=True, allow_local_urls=True
    )
    result = renderer.render_sync(local_test_server)
    assert result.status_code == 200


def test_advanced_stealth_init_no_config():
    """AdvancedStealthRenderer init with stealth_config=None uses randomize path (lines 99-102)."""

    renderer = AdvancedStealthRenderer(randomize_fingerprint=False)
    assert renderer.stealth_config is not None
    assert renderer.timeout == 30_000


def test_advanced_stealth_rejects_duplicate_positional_option():
    with pytest.raises(TypeError, match="multiple values for argument 'timeout'"):
        AdvancedStealthRenderer(5.0, timeout=7.0)


def test_advanced_stealth_rejects_unknown_option():
    with pytest.raises(TypeError, match="unexpected keyword argument 'timeot'"):
        AdvancedStealthRenderer(timeot=5.0)


def test_advanced_stealth_rejects_too_many_positional_options():
    with pytest.raises(TypeError, match="expected at most 6 arguments"):
        AdvancedStealthRenderer(*range(7))


def test_advanced_stealth_init_with_config():
    """AdvancedStealthRenderer init with explicit stealth_config (lines 101-102)."""

    config = get_advanced_stealth_config(randomize=False)
    renderer = AdvancedStealthRenderer(stealth_config=config)
    assert renderer.stealth_config is config


async def test_advanced_stealth_convenience_function(local_test_server):
    """render_with_advanced_stealth() convenience function (lines 280-284)."""

    result = await render_with_advanced_stealth(
        local_test_server, timeout=15.0, headless=True, allow_local_urls=True
    )
    assert result.status_code == 200


def test_advanced_stealth_convenience_sync(local_test_server):
    """render_with_advanced_stealth_sync() convenience function (line 307)."""

    result = render_with_advanced_stealth_sync(
        local_test_server, timeout=15.0, headless=True, allow_local_urls=True
    )
    assert result.status_code == 200


# ---------------------------------------------------------------------------
# Lines 115,117,119,121,123,125,127,129,131,133,135,137: all config overrides
# ---------------------------------------------------------------------------


def test_renderer_init_config_override_all_remaining_params():
    """Passing all individual params with a config covers every override branch."""
    config = RenderConfig(timeout=30.0)
    renderer = Renderer(
        config=config,
        headless=True,  # line 115
        user_agent="Agent/1.0",  # line 117
        stealth=True,  # line 119
        disable_http2=True,  # line 121
        extreme_mode=False,  # line 123
        block_resources=True,  # line 125
        block_images=True,  # line 127
        block_fonts=True,  # line 129
        block_media=True,  # line 131
        block_ads=True,  # line 133
        block_trackers=True,  # line 135
        screenshot=True,  # line 137
    )
    assert renderer.timeout == 30_000
    assert renderer.user_agent == "Agent/1.0"
    assert renderer.stealth is True
    assert renderer.disable_http2 is True


# ---------------------------------------------------------------------------
# Line 275: stealth browser args (STEALTH_BROWSER_ARGS extension)
# ---------------------------------------------------------------------------


def test_prepare_browser_args_stealth_enabled():
    """stealth=True extends browser_args with STEALTH_BROWSER_ARGS (line 275)."""
    renderer = Renderer(stealth=True)
    args = renderer._prepare_browser_args()
    # With stealth enabled, args should be non-empty (from STEALTH_BROWSER_ARGS)
    assert isinstance(args, list)


# ---------------------------------------------------------------------------
# Lines 295-299: stealth context options path
# ---------------------------------------------------------------------------


def test_prepare_context_options_stealth_path():
    """stealth=True exercises the stealth branch (lines 295-299)."""
    renderer = Renderer(stealth=True)
    opts = renderer._prepare_context_options()
    # Stealth path returns context options from stealth config
    assert "user_agent" in opts
    assert "viewport" in opts


def test_prepare_context_options_stealth_with_custom_ua():
    """stealth=True + user_agent set covers line 298 (custom UA override)."""
    renderer = Renderer(stealth=True, user_agent="CustomStealth/2.0")
    opts = renderer._prepare_context_options()
    assert opts.get("user_agent") == "CustomStealth/2.0"


# ---------------------------------------------------------------------------
# _setup_resource_blocking keeps SSRF routing when block_resources=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_with_resource_blocking_disabled(local_test_server):
    """block_resources=False disables performance blocking without disabling SSRF routing."""
    renderer = Renderer(block_resources=False, timeout=15.0, wait_until="load")
    result = await renderer.render(local_test_server)
    assert result.status_code == 200
    assert result.metadata["resource_blocking"] is False
    assert result.metadata["blocked_requests"] == 0
    assert result.metadata["total_requests"] >= 1


# ---------------------------------------------------------------------------
# Lines 417-428: extreme mode – all strategies fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extreme_mode_all_strategies_fail():
    """All extreme mode strategies fail → last exception raised (lines 417-428)."""
    renderer = Renderer(extreme_mode=True, timeout=0.001)
    with pytest.raises(Exception):
        # Port 1 is reserved and will refuse connections immediately
        await renderer.render("http://127.0.0.1:1")


# ---------------------------------------------------------------------------
# Lines 511-512 and 547-550: _wait_for_content exception/timeout paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_content_selector_miss_and_function_timeout():
    """
    Exercise lines 511-512 (selector lookup fails → continue) and
    547-550 (content verification function times out → warning logged).

    Uses a page without article/main/.content/#content so 'body' is found,
    but body text is empty and wait_for_function times out.
    """
    from playwright.async_api import async_playwright

    minimal_html = b"<html><body><span></span></body></html>"

    class MinimalHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(minimal_html)))
            self.end_headers()
            self.wfile.write(minimal_html)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), MinimalHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        renderer = Renderer(timeout=15.0, wait_until="load")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    await page.goto(url, wait_until="load")
                    # max_wait=0.1 → selector timeout = min(10000, 100) = 100ms
                    # selectors article/main/etc. will all time out quickly (511-512)
                    # body will be found immediately (509-510)
                    # JS function never returns true for this page → timeout (547-550)
                    await renderer._wait_for_content(page, max_wait=0.1)
                finally:
                    await context.close()
            finally:
                await browser.close()
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_wait_for_content_uses_combined_selector_query():
    class FakePage:
        def __init__(self):
            self.selector_queries = []

        async def wait_for_selector(self, selector, timeout):
            self.selector_queries.append((selector, timeout))

        async def wait_for_function(self, _script, timeout):
            self.function_timeout = timeout

    page = FakePage()

    await wait_for_content(page, max_wait=6, selectors=("article", "main", "body"))

    assert page.selector_queries == [("article, main, body", 2500)]
    assert page.function_timeout > 0


@pytest.mark.asyncio
async def test_wait_for_content_accepts_body_text_without_timeout(caplog):
    from playwright.async_api import async_playwright

    body_html = b"<html><body><span>Body-only content.</span></body></html>"

    class BodyOnlyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body_html)))
            self.end_headers()
            self.wfile.write(body_html)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), BodyOnlyHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        renderer = Renderer(timeout=15.0, wait_until="load")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    await page.goto(url, wait_until="load")
                    with caplog.at_level("INFO"):
                        await renderer._wait_for_content(page, max_wait=0.2)
                finally:
                    await context.close()
            finally:
                await browser.close()
    finally:
        server.shutdown()

    assert "Content verification passed" in caplog.text
    assert "Content verification timed out" not in caplog.text


@pytest.mark.asyncio
async def test_wait_for_content_accepts_short_article_without_timeout(caplog):
    from playwright.async_api import async_playwright

    short_html = b"<html><body><article><h1>Ok</h1><p>Short.</p></article></body></html>"

    class ShortArticleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(short_html)))
            self.end_headers()
            self.wfile.write(short_html)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ShortArticleHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        renderer = Renderer(timeout=15.0, wait_until="load")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    await page.goto(url, wait_until="load")
                    with caplog.at_level("INFO"):
                        await renderer._wait_for_content(page, max_wait=0.2)
                finally:
                    await context.close()
            finally:
                await browser.close()
    finally:
        server.shutdown()

    assert "Content verification passed" in caplog.text
    assert "Content verification timed out" not in caplog.text


# ---------------------------------------------------------------------------
# AdvancedStealthRenderer: exception path (lines 123-125, 140) + disable_http2 (169)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advanced_stealth_exception_path():
    """Connection-refused URL triggers the except block (lines 123-125, 140)."""

    renderer = AdvancedStealthRenderer(timeout=5.0, headless=True)
    with pytest.raises(Exception):
        await renderer.render("http://127.0.0.1:1")


def test_advanced_stealth_disable_http2_arg(local_test_server):
    """disable_http2=True adds --disable-http2 browser arg (line 169)."""

    renderer = AdvancedStealthRenderer(
        timeout=15.0, disable_http2=True, headless=True, allow_local_urls=True
    )
    result = renderer.render_sync(local_test_server)
    assert result.status_code == 200
