"""Support helpers for the Playwright renderer."""

from __future__ import annotations

import logging
import time
from typing import Any, Final, cast

from markdown_ingress.config_models import RenderConfig
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)
_SCREENSHOT_UNSET: Final = object()

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
        await resource.close()
    except Exception as exc:  # pragma: no cover - defensive cleanup path
        logger.warning("Failed to close %s cleanly: %s", label, exc)


def build_renderer_config(
    default_wait_until: str,
    *,
    config: RenderConfig | None = None,
    timeout: float | None = None,
    wait_until: str | None = None,
    headless: bool | None = None,
    user_agent: str | None = None,
    stealth: bool | None = None,
    disable_http2: bool | None = None,
    extreme_mode: bool | None = None,
    block_resources: bool | None = None,
    block_images: bool | None = None,
    block_fonts: bool | None = None,
    block_media: bool | None = None,
    block_ads: bool | None = None,
    block_trackers: bool | None = None,
    screenshot: bool | str | None = _SCREENSHOT_UNSET,  # type: ignore[assignment]
    allow_local_urls: bool | None = None,
) -> RenderConfig:
    """Normalize init inputs into a concrete RenderConfig."""
    if config is None:
        return RenderConfig(
            timeout=timeout if timeout is not None else 30.0,
            wait_until=wait_until if wait_until is not None else default_wait_until,
            headless=headless if headless is not None else True,
            user_agent=user_agent,
            stealth=stealth if stealth is not None else False,
            disable_http2=disable_http2 if disable_http2 is not None else False,
            extreme_mode=extreme_mode if extreme_mode is not None else False,
            block_resources=block_resources if block_resources is not None else True,
            block_images=block_images if block_images is not None else True,
            block_fonts=block_fonts if block_fonts is not None else True,
            block_media=block_media if block_media is not None else True,
            block_ads=block_ads if block_ads is not None else True,
            block_trackers=block_trackers if block_trackers is not None else True,
            screenshot=None if screenshot is _SCREENSHOT_UNSET else screenshot,
            allow_local_urls=allow_local_urls,
        )

    from dataclasses import replace

    overrides: dict[str, Any] = {}
    if timeout is not None:
        overrides["timeout"] = timeout
    if wait_until is not None:
        overrides["wait_until"] = wait_until
    if headless is not None:
        overrides["headless"] = headless
    if user_agent is not None:
        overrides["user_agent"] = user_agent
    if stealth is not None:
        overrides["stealth"] = stealth
    if disable_http2 is not None:
        overrides["disable_http2"] = disable_http2
    if extreme_mode is not None:
        overrides["extreme_mode"] = extreme_mode
    if block_resources is not None:
        overrides["block_resources"] = block_resources
    if block_images is not None:
        overrides["block_images"] = block_images
    if block_fonts is not None:
        overrides["block_fonts"] = block_fonts
    if block_media is not None:
        overrides["block_media"] = block_media
    if block_ads is not None:
        overrides["block_ads"] = block_ads
    if block_trackers is not None:
        overrides["block_trackers"] = block_trackers
    if screenshot is not _SCREENSHOT_UNSET:
        overrides["screenshot"] = screenshot
    if allow_local_urls is not None:
        overrides["allow_local_urls"] = allow_local_urls
    return replace(config, **overrides) if overrides else config


async def execute_render_session(
    renderer, url: str, timeout_ms: int, *, smart_wait: bool = False
) -> FetchResult:
    """Run a single Playwright browser session using renderer-bound helpers."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:  # pragma: no cover
        raise ImportError(  # pragma: no cover
            "Playwright is not installed. Install with: "
            "pip install 'markdown-ingress[render]' or pip install playwright && playwright install"
        )

    start_time = time.perf_counter()
    browser = None
    context = None
    page = None
    async with async_playwright() as playwright:
        browser_args = renderer._prepare_browser_args()
        launch_options = renderer._prepare_launch_options(browser_args)
        browser = await playwright.chromium.launch(**cast(dict[str, Any], launch_options))
        try:
            context_options = renderer._prepare_context_options()
            context = await browser.new_context(**context_options)
            page = await context.new_page()
            # Inject stealth scripts if enabled and available
            if renderer.stealth and STEALTH_INJECT_AVAILABLE:
                await inject_stealth_pre_nav(page)
            blocker = await renderer._setup_resource_blocking(page)
            response = await renderer._navigate_page(page, url, timeout_ms)
            if renderer.stealth and STEALTH_INJECT_AVAILABLE:
                await inject_stealth_post_nav(page)
            max_wait = min(8 if smart_wait else 6, max(2, timeout_ms // 1000))
            await renderer._wait_for_content(page, max_wait=max_wait)
            html = await renderer._extract_page_content(page)
            try:
                screenshot_path = await renderer._capture_screenshot(page)
            except Exception as e:
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
