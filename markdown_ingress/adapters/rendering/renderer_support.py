"""Support helpers for the Playwright renderer."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Final, cast

import httpx

from markdown_ingress.config_models import RenderConfig
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)
_SCREENSHOT_UNSET: Final[Any] = object()
_MIN_CHROMIUM_LAUNCH_TIMEOUT_MS: Final[int] = 1000
_CHROMIUM_LAUNCH_RETRIES: Final[int] = 1
_RESOURCE_CLOSE_TIMEOUT_S: Final[float] = 1.0

# Lazy import for stealth injection
try:
    from markdown_ingress.core.stealth.js_injection import (
        inject_stealth_post_nav,
        inject_stealth_pre_nav,
    )

    STEALTH_INJECT_AVAILABLE = True
except ImportError:  # pragma: no cover
    STEALTH_INJECT_AVAILABLE = False  # pragma: no cover


async def _close_async_resource(resource: Any | None, label: str) -> None:
    """Close an async Playwright resource without masking earlier failures."""
    if resource is None:
        return
    try:
        await asyncio.wait_for(resource.close(), timeout=_RESOURCE_CLOSE_TIMEOUT_S)
    except TimeoutError:
        logger.warning("Timed out closing %s cleanly", label)
    except Exception as exc:  # noqa: BLE001 - cleanup must not mask prior failures
        logger.warning("Failed to close %s cleanly: %s", label, exc)


async def launch_chromium(chromium: Any, launch_options: dict[str, object], timeout_ms: int) -> Any:
    """Launch Chromium with a bounded timeout and one retry for launch hangs."""
    options = dict(launch_options)
    options.setdefault(
        "timeout",
        max(_MIN_CHROMIUM_LAUNCH_TIMEOUT_MS, int(timeout_ms)),
    )

    for attempt in range(_CHROMIUM_LAUNCH_RETRIES + 1):
        try:
            return await chromium.launch(**options)
        except Exception as exc:
            if attempt >= _CHROMIUM_LAUNCH_RETRIES or not _is_launch_timeout(exc):
                raise
            logger.warning("Chromium launch timed out; retrying once: %s", exc)
            await asyncio.sleep(0.1)

    raise RuntimeError("Chromium launch retry loop exited unexpectedly")


def _is_launch_timeout(exc: Exception) -> bool:
    message = str(exc).lower()
    return "launch" in message and "timeout" in message


def raise_for_render_status(response: Any | None, url: str) -> None:
    """Raise an HTTPStatusError when Playwright navigates to an error response."""
    if response is None:
        return
    status_code = getattr(response, "status", None)
    if status_code is None:
        return
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        return
    if status_code < 400:
        return

    request = httpx.Request("GET", url)
    http_response = httpx.Response(
        status_code,
        headers=getattr(response, "headers", None) or {},
        request=request,
    )
    http_response.raise_for_status()


@dataclass(frozen=True)
class RendererConfigInputs:
    """Optional RenderConfig plus per-field overrides supplied by renderer constructors."""

    config: RenderConfig | None = None
    timeout: float | None = None
    wait_until: str | None = None
    headless: bool | None = None
    user_agent: str | None = None
    stealth: bool | None = None
    disable_http2: bool | None = None
    extreme_mode: bool | None = None
    block_resources: bool | None = None
    block_images: bool | None = None
    block_fonts: bool | None = None
    block_media: bool | None = None
    block_ads: bool | None = None
    block_trackers: bool | None = None
    screenshot: Any = _SCREENSHOT_UNSET
    allow_local_urls: bool | None = None


def build_renderer_config(
    default_wait_until: str,
    inputs: RendererConfigInputs,
) -> RenderConfig:
    """Normalize init inputs into a concrete RenderConfig."""
    if inputs.config is None:
        return _build_default_renderer_config(default_wait_until, inputs)

    overrides = _collect_renderer_config_overrides(inputs)
    return replace(inputs.config, **overrides) if overrides else inputs.config


def _build_default_renderer_config(
    default_wait_until: str,
    inputs: RendererConfigInputs,
) -> RenderConfig:
    return RenderConfig(
        timeout=inputs.timeout if inputs.timeout is not None else 30.0,
        wait_until=inputs.wait_until if inputs.wait_until is not None else default_wait_until,
        headless=inputs.headless if inputs.headless is not None else True,
        user_agent=inputs.user_agent,
        stealth=inputs.stealth if inputs.stealth is not None else False,
        disable_http2=inputs.disable_http2 if inputs.disable_http2 is not None else False,
        extreme_mode=inputs.extreme_mode if inputs.extreme_mode is not None else False,
        block_resources=inputs.block_resources if inputs.block_resources is not None else True,
        block_images=inputs.block_images if inputs.block_images is not None else True,
        block_fonts=inputs.block_fonts if inputs.block_fonts is not None else True,
        block_media=inputs.block_media if inputs.block_media is not None else True,
        block_ads=inputs.block_ads if inputs.block_ads is not None else True,
        block_trackers=inputs.block_trackers if inputs.block_trackers is not None else True,
        screenshot=None if inputs.screenshot is _SCREENSHOT_UNSET else inputs.screenshot,
        allow_local_urls=inputs.allow_local_urls,
    )


def _collect_renderer_config_overrides(inputs: RendererConfigInputs) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        name: value
        for name, value in (
            ("timeout", inputs.timeout),
            ("wait_until", inputs.wait_until),
            ("headless", inputs.headless),
            ("user_agent", inputs.user_agent),
            ("stealth", inputs.stealth),
            ("disable_http2", inputs.disable_http2),
            ("extreme_mode", inputs.extreme_mode),
            ("block_resources", inputs.block_resources),
            ("block_images", inputs.block_images),
            ("block_fonts", inputs.block_fonts),
            ("block_media", inputs.block_media),
            ("block_ads", inputs.block_ads),
            ("block_trackers", inputs.block_trackers),
            ("allow_local_urls", inputs.allow_local_urls),
        )
        if value is not None
    }
    if inputs.screenshot is not _SCREENSHOT_UNSET:
        overrides["screenshot"] = inputs.screenshot
    return overrides


async def execute_render_session(
    renderer, url: str, timeout_ms: int, *, smart_wait: bool = False
) -> FetchResult:
    """Run a single Playwright browser session using renderer-bound helpers."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        raise ImportError(  # pragma: no cover
            "Playwright is not installed. Install with: "
            "pip install 'markdown-ingress[render]' or pip install playwright && playwright install"
        ) from exc

    start_time = time.perf_counter()
    browser = None
    context = None
    page = None
    async with async_playwright() as playwright:
        browser_args = renderer._prepare_browser_args()
        launch_options = renderer._prepare_launch_options(browser_args)
        browser = await launch_chromium(
            playwright.chromium,
            cast(dict[str, object], launch_options),
            timeout_ms,
        )
        try:
            context_options = renderer._prepare_context_options()
            context = await browser.new_context(**context_options)
            page = await context.new_page()
            # Inject stealth scripts if enabled and available
            if renderer.stealth and STEALTH_INJECT_AVAILABLE:
                await inject_stealth_pre_nav(page)
            blocker = await renderer._setup_resource_blocking(page)
            response = await renderer._navigate_page(page, url, timeout_ms)
            raise_for_render_status(response, url)
            if renderer.stealth and STEALTH_INJECT_AVAILABLE:
                await inject_stealth_post_nav(page)
            max_wait = min(8 if smart_wait else 6, max(2, timeout_ms // 1000))
            await renderer._wait_for_content(page, max_wait=max_wait)
            html = await renderer._extract_page_content(page)
            try:
                screenshot_path = await renderer._capture_screenshot(page)
            except Exception as e:  # noqa: BLE001 - screenshots are optional metadata
                logger.warning("Screenshot capture failed, continuing without: %s", e)
                screenshot_path = None

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            metadata = renderer._build_metadata(screenshot_path, blocker)
            if smart_wait:
                metadata["smart_wait_used"] = True

            return FetchResult(
                html=html,
                url=url,
                status_code=response.status if response else 200,
                final_url=page.url,
                headers=response.headers if response else {},
                timing_ms=elapsed_ms,
                metadata=metadata,
            )
        finally:
            # Close each resource independently so one failure does not mask
            # or skip the remaining cleanup steps.
            await _close_async_resource(page, "page")
            await _close_async_resource(context, "browser context")
            await _close_async_resource(browser, "browser")
