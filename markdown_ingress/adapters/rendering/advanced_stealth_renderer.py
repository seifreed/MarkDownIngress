"""Advanced stealth Playwright renderer — implements IRenderer protocol."""

import asyncio
import logging
import time
from typing import Any, Literal, cast

from markdown_ingress.core.resource_blocker import ResourceBlocker
from markdown_ingress.core.ssrf import (
    resolve_allow_local_urls,
    validate_http_url_no_ssrf,
    validated_http_url_requires_dns_pinning,
)
from markdown_ingress.core.stealth import (
    AdvancedStealthConfig,
    get_advanced_context_options,
    get_advanced_stealth_config,
    inject_stealth_post_nav,
    inject_stealth_pre_nav,
)
from markdown_ingress.models import FetchResult

WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]

logger = logging.getLogger(__name__)


async def _close_async_resource(resource: Any | None, label: str) -> None:
    """Close an async Playwright resource without masking earlier failures."""
    if resource is None:
        return
    try:
        await resource.close()
    except Exception as exc:  # pragma: no cover - defensive cleanup path
        logger.warning("Failed to close %s cleanly: %s", label, exc)


class AdvancedStealthRenderer:
    """
    Advanced Playwright renderer with maximum bot detection evasion.

    Features:
    - Ultra stealth browser arguments
    - Comprehensive JavaScript injection
    - Randomized browser fingerprints
    - Realistic HTTP headers
    - Smart waiting strategies
    - Automatic retry logic
    - HTTP/2 fallback support

    Example:
        >>> renderer = AdvancedStealthRenderer()
        >>> result = await renderer.render("https://example.com")
        >>> print(result.html)
    """

    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "networkidle"

    def __init__(
        self,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
        headless: bool = True,
        randomize_fingerprint: bool = True,
        disable_http2: bool = False,
        stealth_config: AdvancedStealthConfig | None = None,
        *,
        allow_local_urls: bool | None = None,
        block_resources: bool = True,
        block_images: bool = True,
        block_fonts: bool = True,
        block_media: bool = True,
        block_ads: bool = True,
        block_trackers: bool = True,
    ):
        self.timeout = int(timeout * 1000)  # Convert to milliseconds
        self.wait_until = wait_until
        self.headless = headless
        self.randomize_fingerprint = randomize_fingerprint
        self.disable_http2 = disable_http2
        self.allow_local_urls = resolve_allow_local_urls(allow_local_urls)
        self.block_resources = block_resources
        self.block_images = block_images
        self.block_fonts = block_fonts
        self.block_media = block_media
        self.block_ads = block_ads
        self.block_trackers = block_trackers

        if stealth_config is None:
            self.stealth_config = get_advanced_stealth_config(randomize=randomize_fingerprint)
        else:
            self.stealth_config = stealth_config

    def _validate_render_url(self, url: str) -> str:
        validated_url = validate_http_url_no_ssrf(
            url,
            allow_local=self.allow_local_urls,
            resolve_dns=True,
        )
        if validated_http_url_requires_dns_pinning(url, validated_url):
            raise ValueError("Render URL requires DNS pinning that cannot be preserved safely")
        return str(url).strip()

    async def render(self, url: str) -> FetchResult:
        """
        Render URL using advanced stealth techniques.

        Includes automatic retry logic and HTTP/2 fallback.
        """
        validated_url = self._validate_render_url(url)
        try:
            result = await self._render_with_browser(validated_url)
            return result
        except Exception as e:
            error_str = str(e)
            if (
                "ERR_HTTP2_PROTOCOL_ERROR" in error_str and not self.disable_http2
            ):  # pragma: no cover
                retry_renderer = AdvancedStealthRenderer(  # pragma: no cover
                    timeout=self.timeout / 1000.0,
                    wait_until=self.wait_until,
                    headless=self.headless,
                    randomize_fingerprint=self.randomize_fingerprint,
                    disable_http2=True,
                    stealth_config=self.stealth_config,
                    allow_local_urls=self.allow_local_urls,
                    block_resources=self.block_resources,
                    block_images=self.block_images,
                    block_fonts=self.block_fonts,
                    block_media=self.block_media,
                    block_ads=self.block_ads,
                    block_trackers=self.block_trackers,
                )
                result = await retry_renderer._render_with_browser(validated_url)
                result.metadata["http2_fallback"] = True
                result.metadata["original_error"] = "ERR_HTTP2_PROTOCOL_ERROR"
                return result
            raise

    async def _setup_resource_blocking(self, page):
        block_images = self.block_images if self.block_resources else False
        block_fonts = self.block_fonts if self.block_resources else False
        block_media = self.block_media if self.block_resources else False
        block_ads = self.block_ads if self.block_resources else False
        block_trackers = self.block_trackers if self.block_resources else False
        blocker = ResourceBlocker(
            block_images=block_images,
            block_fonts=block_fonts,
            block_media=block_media,
            block_ads=block_ads,
            block_trackers=block_trackers,
            allow_local_urls=self.allow_local_urls,
            validate_ssrf=True,
        )
        await blocker.setup_blocking(page)
        return blocker

    async def _render_with_browser(self, url: str) -> FetchResult:
        """Internal method to render URL with browser."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:  # pragma: no cover
            raise ImportError(  # pragma: no cover
                "Playwright is not installed. Install with: "
                "pip install 'markdown-ingress[render]' or "
                "pip install playwright && playwright install"
            )  # pragma: no cover

        start_time = time.perf_counter()

        async with async_playwright() as p:
            browser_args = self.stealth_config.browser_args.copy()

            if self.disable_http2:
                browser_args.append("--disable-http2")

            launch_options: dict[str, object] = {
                "headless": self.headless,
                "args": browser_args,
                "ignore_default_args": ["--enable-automation"],
            }

            browser = None
            browser = await p.chromium.launch(**cast(dict[str, Any], launch_options))
            context = None
            page = None

            try:
                context_options = get_advanced_context_options(self.stealth_config)
                context = await browser.new_context(**context_options)

                try:
                    page = await context.new_page()
                    await inject_stealth_pre_nav(page)
                    blocker = await self._setup_resource_blocking(page)

                    response = await page.goto(
                        url, timeout=self.timeout, wait_until=cast(WaitUntil, self.wait_until)
                    )

                    await inject_stealth_post_nav(page)
                    await page.wait_for_timeout(500)

                    final_url = page.url
                    status_code = response.status if response else 200
                    html = await page.content()
                    headers = response.headers if response else {}

                    elapsed_ms = (time.perf_counter() - start_time) * 1000

                    metadata = {
                        "renderer": "advanced_stealth_playwright",
                        "user_agent": self.stealth_config.user_agent,
                        "viewport": f"{self.stealth_config.viewport_width}x{self.stealth_config.viewport_height}",
                        "device_scale_factor": self.stealth_config.device_scale_factor,
                        "timezone": self.stealth_config.timezone,
                        "http2_disabled": self.disable_http2,
                        "stealth_injected": True,
                    }
                    if blocker is not None:
                        metadata["resource_blocking"] = blocker.get_stats()

                    return FetchResult(
                        html=html,
                        url=url,
                        status_code=status_code,
                        final_url=final_url,
                        headers=headers,
                        timing_ms=elapsed_ms,
                        metadata=metadata,
                    )

                finally:
                    await _close_async_resource(page, "page")
                    await _close_async_resource(context, "browser context")

            finally:
                await _close_async_resource(browser, "browser")

    def render_sync(self, url: str) -> FetchResult:
        """Synchronous wrapper for render()."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.render(url))

        raise RuntimeError(
            "render_sync() cannot run inside an active event loop; await render() instead"
        )


async def render_with_advanced_stealth(
    url: str,
    timeout: float = 30.0,
    headless: bool = True,
    *,
    allow_local_urls: bool | None = None,
    block_resources: bool = True,
    block_images: bool = True,
    block_fonts: bool = True,
    block_media: bool = True,
    block_ads: bool = True,
    block_trackers: bool = True,
) -> FetchResult:
    """Convenience function to render a URL with advanced stealth."""
    renderer = AdvancedStealthRenderer(
        timeout=timeout,
        headless=headless,
        allow_local_urls=allow_local_urls,
        block_resources=block_resources,
        block_images=block_images,
        block_fonts=block_fonts,
        block_media=block_media,
        block_ads=block_ads,
        block_trackers=block_trackers,
    )
    return await renderer.render(url)


def render_with_advanced_stealth_sync(
    url: str,
    timeout: float = 30.0,
    headless: bool = True,
    *,
    allow_local_urls: bool | None = None,
    block_resources: bool = True,
    block_images: bool = True,
    block_fonts: bool = True,
    block_media: bool = True,
    block_ads: bool = True,
    block_trackers: bool = True,
) -> FetchResult:
    """Synchronous convenience function for advanced stealth rendering."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            render_with_advanced_stealth(
                url,
                timeout,
                headless,
                allow_local_urls=allow_local_urls,
                block_resources=block_resources,
                block_images=block_images,
                block_fonts=block_fonts,
                block_media=block_media,
                block_ads=block_ads,
                block_trackers=block_trackers,
            )
        )

    raise RuntimeError(
        "render_with_advanced_stealth_sync() cannot run inside an active event loop; "
        "await render_with_advanced_stealth() instead"
    )
