"""Cleanup regressions for Playwright-backed renderers."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from markdown_ingress.core.advanced_stealth import AdvancedStealthRenderer
from markdown_ingress.core.renderer_support import execute_render_session


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

    async def goto(self, _url: str, **_kwargs):
        return SimpleNamespace(status=200, headers={"content-type": "text/html"})

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
async def test_advanced_stealth_closes_browser_when_context_close_fails(monkeypatch):
    page, context, browser = _install_fake_playwright(
        monkeypatch,
        context_close_error=RuntimeError("context close failed"),
    )

    from markdown_ingress.core import advanced_stealth as advanced_stealth_module

    async def fake_inject_stealth(_page):
        return None

    monkeypatch.setattr(advanced_stealth_module, "inject_stealth_pre_nav", fake_inject_stealth)
    monkeypatch.setattr(advanced_stealth_module, "inject_stealth_post_nav", fake_inject_stealth)

    renderer = AdvancedStealthRenderer(timeout=5.0, headless=True)
    result = await renderer._render_with_browser("https://example.com")

    assert result.status_code == 200
    assert context.closed is True
    assert browser.closed is True


@pytest.mark.asyncio
async def test_advanced_stealth_launch_failure_preserves_original_error(monkeypatch):
    _install_fake_playwright(monkeypatch, launch_error=RuntimeError("launch failed"))

    renderer = AdvancedStealthRenderer(timeout=5.0, headless=True)

    with pytest.raises(RuntimeError, match="launch failed"):
        await renderer._render_with_browser("https://example.com")
