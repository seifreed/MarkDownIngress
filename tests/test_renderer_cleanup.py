"""Cleanup regressions for Playwright-backed renderers."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from markdown_ingress.adapters.rendering.advanced_stealth_renderer import AdvancedStealthRenderer
from markdown_ingress.adapters.rendering.playwright_renderer import Renderer
from markdown_ingress.adapters.rendering.renderer_support import execute_render_session


class _FakeCloseable:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.closed = False
        self._close_error = close_error

    async def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class _FakePage(_FakeCloseable):
    def __init__(self, *, close_error: Exception | None = None) -> None:
        super().__init__(close_error=close_error)
        self.url = "https://example.com/final"
        self.routes: list[tuple[str, object]] = []

    async def goto(self, _url: str, **_kwargs):
        return SimpleNamespace(status=200, headers={"content-type": "text/html"})

    async def route(self, pattern: str, handler) -> None:
        self.routes.append((pattern, handler))

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def content(self) -> str:
        return "<html><body>ok</body></html>"


class _FakeContext(_FakeCloseable):
    def __init__(self, page: _FakePage, *, close_error: Exception | None = None) -> None:
        super().__init__(close_error=close_error)
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page


class _FakeBrowser(_FakeCloseable):
    def __init__(self, context: _FakeContext, *, close_error: Exception | None = None) -> None:
        super().__init__(close_error=close_error)
        self._context = context

    async def new_context(self, **_kwargs) -> _FakeContext:
        return self._context


class _FakeChromium:
    def __init__(
        self, browser: _FakeBrowser | None, *, launch_error: Exception | None = None
    ) -> None:
        self._browser = browser
        self._launch_error = launch_error

    async def launch(self, **_kwargs) -> _FakeBrowser:
        if self._launch_error is not None:
            raise self._launch_error
        assert self._browser is not None
        return self._browser


class _FakePlaywright:
    def __init__(
        self, browser: _FakeBrowser | None, *, launch_error: Exception | None = None
    ) -> None:
        self.chromium = _FakeChromium(browser, launch_error=launch_error)


class _FakeAsyncPlaywrightCM:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self._playwright = playwright

    async def __aenter__(self) -> _FakePlaywright:
        return self._playwright

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _install_fake_playwright(
    monkeypatch,
    *,
    page_close_error=None,
    context_close_error=None,
    launch_error: Exception | None = None,
):
    page = _FakePage(close_error=page_close_error)
    context = _FakeContext(page, close_error=context_close_error)
    browser = _FakeBrowser(context)
    playwright = _FakePlaywright(
        None if launch_error is not None else browser, launch_error=launch_error
    )

    fake_pkg = ModuleType("playwright")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_async_api = ModuleType("playwright.async_api")
    fake_async_api.async_playwright = lambda: _FakeAsyncPlaywrightCM(playwright)
    fake_pkg.async_api = fake_async_api  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)
    return page, context, browser


def _assert_ssrf_blocker_installed_with_performance_blocking_disabled(blocker, page):
    assert blocker is not None
    assert page.routes and page.routes[0][0] == "**/*"
    assert blocker.block_images is False
    assert blocker.block_fonts is False
    assert blocker.block_media is False
    assert blocker.block_ads is False
    assert blocker.block_trackers is False
    assert blocker.validate_ssrf is True
    assert blocker._should_block("document", "http://127.0.0.1/private") == (
        True,
        "ssrf_protection",
    )


class _GuardedRenderer(Renderer):
    async def _render_with_browser(self, url: str):
        raise AssertionError(f"renderer should not navigate to {url}")

    async def _render_with_progressive_timeout(self, url: str):
        raise AssertionError(f"renderer should not navigate to {url}")


@pytest.mark.asyncio
async def test_execute_render_session_closes_context_and_browser_when_page_close_fails(monkeypatch):
    page, context, browser = _install_fake_playwright(
        monkeypatch,
        page_close_error=RuntimeError("page close failed"),
    )

    async def _setup_resource_blocking(_page):
        return None

    async def _navigate_page(_page, _url, _timeout_ms):
        return SimpleNamespace(status=200, headers={"x-test": "ok"})

    async def _wait_for_content(_page, max_wait):
        return None

    async def _extract_page_content(_page):
        return "<html><body>ok</body></html>"

    async def _capture_screenshot(_page):
        return None

    renderer = SimpleNamespace(
        stealth=False,
        _prepare_browser_args=lambda: [],
        _prepare_launch_options=lambda browser_args: {},
        _prepare_context_options=lambda: {},
        _setup_resource_blocking=_setup_resource_blocking,
        _navigate_page=_navigate_page,
        _wait_for_content=_wait_for_content,
        _extract_page_content=_extract_page_content,
        _capture_screenshot=_capture_screenshot,
        _build_metadata=lambda screenshot_path, blocker: {"renderer": "fake"},
    )

    result = await execute_render_session(renderer, "https://example.com", 1000)

    assert result.status_code == 200
    assert page.closed is True
    assert context.closed is True
    assert browser.closed is True


@pytest.mark.asyncio
async def test_renderer_block_resources_false_keeps_subresource_ssrf_blocking():
    page = _FakePage()
    renderer = Renderer(
        block_resources=False,
        allow_local_urls=False,
        timeout=5.0,
        wait_until="load",
    )

    blocker = await renderer._setup_resource_blocking(page)

    _assert_ssrf_blocker_installed_with_performance_blocking_disabled(blocker, page)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/hook",
        "file:///etc/hosts",
        "http://127.0.0.1/private",
    ],
)
def test_renderer_rejects_unsafe_top_level_url_before_navigation(url: str):
    renderer = _GuardedRenderer(allow_local_urls=False)

    with pytest.raises(ValueError):
        renderer.render_sync(url)


def test_renderer_validates_public_top_level_url_with_dns_check(monkeypatch):
    calls: list[tuple[str, bool, bool]] = []

    def fake_validate(url: str, *, allow_local: bool, resolve_dns: bool) -> str:
        assert url == "https://rebind.example/private"
        calls.append((url, allow_local, resolve_dns))
        return "https://93.184.216.34/private"

    monkeypatch.setattr("markdown_ingress.core.ssrf.validate_http_url_no_ssrf", fake_validate)
    renderer = _GuardedRenderer(allow_local_urls=False)

    assert renderer._validate_render_url("https://rebind.example/private") == (
        "https://rebind.example/private"
    )
    assert calls == [("https://rebind.example/private", False, True)]


@pytest.mark.asyncio
async def test_advanced_stealth_block_resources_false_keeps_subresource_ssrf_blocking():
    page = _FakePage()
    renderer = AdvancedStealthRenderer(
        block_resources=False,
        allow_local_urls=False,
        timeout=5.0,
        wait_until="load",
    )

    blocker = await renderer._setup_resource_blocking(page)

    _assert_ssrf_blocker_installed_with_performance_blocking_disabled(blocker, page)


@pytest.mark.asyncio
async def test_advanced_stealth_closes_browser_when_context_close_fails(monkeypatch):
    page, context, browser = _install_fake_playwright(
        monkeypatch,
        context_close_error=RuntimeError("context close failed"),
    )

    import markdown_ingress.adapters.rendering.advanced_stealth_renderer as _renderer_module

    async def fake_inject_stealth(_page):
        return None

    monkeypatch.setattr(_renderer_module, "inject_stealth_pre_nav", fake_inject_stealth)
    monkeypatch.setattr(_renderer_module, "inject_stealth_post_nav", fake_inject_stealth)

    renderer = AdvancedStealthRenderer(timeout=5.0, headless=True)
    result = await renderer._render_with_browser("https://example.com")

    assert result.status_code == 200
    assert page.routes and page.routes[0][0] == "**/*"
    assert context.closed is True
    assert browser.closed is True


@pytest.mark.asyncio
async def test_advanced_stealth_launch_failure_preserves_original_error(monkeypatch):
    _install_fake_playwright(monkeypatch, launch_error=RuntimeError("launch failed"))

    renderer = AdvancedStealthRenderer(timeout=5.0, headless=True)

    with pytest.raises(RuntimeError, match="launch failed"):
        await renderer._render_with_browser("https://example.com")


@pytest.mark.asyncio
async def test_advanced_stealth_rejects_local_top_level_url():
    renderer = AdvancedStealthRenderer(timeout=5.0, headless=True, allow_local_urls=False)

    with pytest.raises(ValueError, match="SSRF protection"):
        await renderer.render("http://127.0.0.1/private")


@pytest.mark.asyncio
async def test_advanced_stealth_allows_public_top_level_url_with_dns_check(monkeypatch):
    calls: list[tuple[str, bool, bool]] = []

    def fake_validate(url: str, *, allow_local: bool, resolve_dns: bool) -> str:
        assert url == "https://rebind.example/private"
        calls.append((url, allow_local, resolve_dns))
        return "https://93.184.216.34/private"

    rendered_urls: list[str] = []

    async def fake_render(_url: str):
        rendered_urls.append(_url)
        return SimpleNamespace(url=_url)

    monkeypatch.setattr("markdown_ingress.core.ssrf.validate_http_url_no_ssrf", fake_validate)
    renderer = AdvancedStealthRenderer(timeout=5.0, headless=True, allow_local_urls=False)
    monkeypatch.setattr(renderer, "_render_with_browser", fake_render)

    await renderer.render("https://rebind.example/private")

    assert calls == [("https://rebind.example/private", False, True)]
    assert rendered_urls == ["https://rebind.example/private"]
