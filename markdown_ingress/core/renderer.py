"""
Playwright-based renderer for SPA/JavaScript-heavy sites
"""

import asyncio
import logging
import tempfile
import time

from markdown_ingress.config_models import RenderConfig
from markdown_ingress.core.resource_blocker import ResourceBlocker
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)

try:
    from markdown_ingress.core.stealth import (
        STEALTH_BROWSER_ARGS,
        get_context_options,
        get_stealth_config,
    )

    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False


class Renderer:  # implements IRenderer protocol
    """Headless browser renderer using Playwright for JavaScript-heavy sites"""

    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "networkidle"  # or "load", "domcontentloaded"

    # Progressive timeout strategies (state, timeout_ms)
    LOAD_STRATEGIES = [
        ("networkidle", 90000),  # Wait for network to be idle
        ("domcontentloaded", 180000),  # Wait for DOM
        ("load", 300000),  # Wait for everything
    ]

    # Fallback selectors for content detection
    CONTENT_SELECTORS = [
        "article",
        "main",
        '[role="main"]',
        ".content",
        "#content",
        "body",
    ]

    def __init__(
        self,
        config: RenderConfig | None = None,
        # Backward compatibility: accept individual parameters
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
        screenshot: bool | str | None = None,
    ):
        """
        Initialize Playwright renderer.

        Args:
            config: RenderConfig object with all settings (recommended)
            timeout: Navigation timeout in seconds (deprecated, use config)
            wait_until: When to consider navigation complete (deprecated, use config)
            headless: Run browser in headless mode (deprecated, use config)
            user_agent: Custom user agent (deprecated, use config)
            stealth: Enable stealth mode (deprecated, use config)
            disable_http2: Disable HTTP/2 protocol (deprecated, use config)
            extreme_mode: Enable extreme timeouts (deprecated, use config)
            block_resources: Enable resource blocking (deprecated, use config)
            block_images: Block images (deprecated, use config)
            block_fonts: Block fonts (deprecated, use config)
            block_media: Block media (deprecated, use config)
            block_ads: Block ads (deprecated, use config)
            block_trackers: Block trackers (deprecated, use config)
            screenshot: Screenshot path or True for temp (deprecated, use config)
        """
        # If no config provided, create from individual parameters or defaults
        if config is None:
            config = RenderConfig(
                timeout=timeout if timeout is not None else 30.0,
                wait_until=wait_until if wait_until is not None else "networkidle",
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
                screenshot=screenshot,
            )
        else:
            # Config provided - override with any explicit parameters
            if timeout is not None:
                config.timeout = timeout
            if wait_until is not None:
                config.wait_until = wait_until
            if headless is not None:
                config.headless = headless
            if user_agent is not None:
                config.user_agent = user_agent
            if stealth is not None:
                config.stealth = stealth
            if disable_http2 is not None:
                config.disable_http2 = disable_http2
            if extreme_mode is not None:
                config.extreme_mode = extreme_mode
            if block_resources is not None:
                config.block_resources = block_resources
            if block_images is not None:
                config.block_images = block_images
            if block_fonts is not None:
                config.block_fonts = block_fonts
            if block_media is not None:
                config.block_media = block_media
            if block_ads is not None:
                config.block_ads = block_ads
            if block_trackers is not None:
                config.block_trackers = block_trackers
            if screenshot is not None:
                config.screenshot = screenshot

        # Store configuration
        self.timeout = int(config.timeout * 1000)  # Convert to milliseconds
        self.wait_until = config.wait_until
        self.headless = config.headless
        self.stealth = config.stealth
        self.disable_http2 = config.disable_http2
        self.extreme_mode = config.extreme_mode
        self.user_agent = (
            config.user_agent
            or "Mozilla/5.0 (compatible; MarkDownIngress/0.2; +https://github.com/markdowningress)"
        )
        self.block_resources = config.block_resources
        self.block_images = config.block_images
        self.block_fonts = config.block_fonts
        self.block_media = config.block_media
        self.block_ads = config.block_ads
        self.block_trackers = config.block_trackers
        self.screenshot = config.screenshot

    async def render(self, url: str) -> FetchResult:
        """
        Render URL using Playwright and return HTML after JavaScript execution.
        Includes automatic HTTP/2 fallback on protocol errors and progressive timeout strategies.

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata

        Raises:
            ImportError: If playwright is not installed
            playwright._impl._errors.TimeoutError: On timeout
            playwright._impl._errors.Error: On navigation errors (except HTTP/2 errors which trigger fallback)
        """
        # Use progressive timeout strategy if extreme mode is enabled
        if self.extreme_mode:
            return await self._render_with_progressive_timeout(url)

        try:
            result = await self._render_with_browser(url)
            return result
        except Exception as e:
            error_str = str(e)
            # Check for HTTP/2 protocol error
            if "ERR_HTTP2_PROTOCOL_ERROR" in error_str and not self.disable_http2:
                # Retry with HTTP/2 disabled - create config from current settings
                retry_config = RenderConfig(
                    timeout=self.timeout / 1000.0,  # Convert back to seconds
                    wait_until=self.wait_until,
                    headless=self.headless,
                    user_agent=self.user_agent,
                    stealth=self.stealth,
                    disable_http2=True,
                    extreme_mode=self.extreme_mode,
                    block_resources=self.block_resources,
                    block_images=self.block_images,
                    block_fonts=self.block_fonts,
                    block_media=self.block_media,
                    block_ads=self.block_ads,
                    block_trackers=self.block_trackers,
                    screenshot=self.screenshot,
                )
                retry_renderer = Renderer(config=retry_config)
                result = await retry_renderer._render_with_browser(url)
                # Mark as HTTP/2 fallback
                result.metadata["http2_fallback"] = True
                result.metadata["original_error"] = "ERR_HTTP2_PROTOCOL_ERROR"
                return result
            # Re-raise if not HTTP/2 error or if already retried
            raise

    async def _render_with_browser(self, url: str) -> FetchResult:
        """
        Internal method to render URL with browser.

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Install with: "
                "pip install 'markdown-ingress[render]' or pip install playwright && playwright install"
            )

        start_time = time.perf_counter()

        async with async_playwright() as p:
            browser_args = self._prepare_browser_args()
            launch_options = self._prepare_launch_options(browser_args)
            browser = await p.chromium.launch(**launch_options)

            try:
                context_options = self._prepare_context_options()
                context = await browser.new_context(**context_options)

                try:
                    page = await context.new_page()
                    blocker = await self._setup_resource_blocking(page)

                    response = await page.goto(
                        url, timeout=self.timeout, wait_until=self.wait_until
                    )

                    html = await page.content()
                    screenshot_path = await self._capture_screenshot(page)

                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    metadata = self._build_metadata(screenshot_path, blocker)

                    return FetchResult(
                        html=html,
                        url=url,
                        status_code=response.status if response else 200,
                        final_url=page.url,
                        headers=dict(response.headers) if response else {},
                        timing_ms=elapsed_ms,
                        metadata=metadata,
                    )

                finally:
                    await context.close()

            finally:
                await browser.close()

    def _prepare_browser_args(self) -> list[str]:
        """Prepare browser launch arguments based on configuration."""
        browser_args = []

        if self.stealth and STEALTH_AVAILABLE:
            browser_args.extend(STEALTH_BROWSER_ARGS)

        if self.disable_http2:
            browser_args.append("--disable-http2")

        return browser_args

    def _prepare_launch_options(self, browser_args: list[str]) -> dict:
        """Prepare browser launch options."""
        launch_options = {"headless": self.headless}

        if browser_args:
            launch_options["args"] = browser_args
            launch_options["ignore_default_args"] = ["--enable-automation"]

        return launch_options

    def _prepare_context_options(self) -> dict:
        """Prepare browser context options based on stealth configuration."""
        if self.stealth and STEALTH_AVAILABLE:
            stealth_config = get_stealth_config()
            context_options = get_context_options(stealth_config)
            if self.user_agent:
                context_options["user_agent"] = self.user_agent
            return context_options

        return {
            "user_agent": self.user_agent,
            "viewport": {"width": 1920, "height": 1080},
            "bypass_csp": True,
            "ignore_https_errors": True,
        }

    async def _setup_resource_blocking(self, page):
        """Setup resource blocking on the page if enabled."""
        if not self.block_resources:
            return None

        blocker = ResourceBlocker(
            block_images=self.block_images,
            block_fonts=self.block_fonts,
            block_media=self.block_media,
            block_ads=self.block_ads,
            block_trackers=self.block_trackers,
        )
        await blocker.setup_blocking(page)
        return blocker

    async def _capture_screenshot(self, page) -> str | None:
        """Capture page screenshot if requested."""
        if not self.screenshot:
            return None

        if self.screenshot is True:
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".png", delete=False, prefix="mdingress_screenshot_"
            )
            screenshot_path = temp_file.name
            temp_file.close()
        else:
            screenshot_path = self.screenshot

        await page.screenshot(path=screenshot_path, full_page=True)
        return screenshot_path

    def _build_metadata(self, screenshot_path: str | None, blocker) -> dict:
        """Build metadata dictionary for the fetch result."""
        metadata = {
            "renderer": "playwright",
            "stealth_mode": self.stealth,
            "http2_disabled": self.disable_http2,
        }

        if screenshot_path:
            metadata["screenshot_path"] = screenshot_path

        if blocker:
            stats = blocker.get_stats()
            metadata.update({
                "resource_blocking": True,
                "blocked_requests": stats["blocked_requests"],
                "total_requests": stats["total_requests"],
                "block_rate_pct": stats["block_rate_pct"],
                "blocked_by_type": stats["blocked_by_type"],
            })

        return metadata

    async def _render_with_progressive_timeout(self, url: str) -> FetchResult:
        """
        Try rendering with progressively longer timeouts.

        Strategy:
        1. Try 90s with 'networkidle'
        2. Try 180s with 'domcontentloaded'
        3. Try 300s with 'load'

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata
        """
        last_exception = None

        for strategy_index, (wait_state, timeout_ms) in enumerate(self.LOAD_STRATEGIES):
            try:
                logger.info(
                    f"[Extreme Mode] Attempt {strategy_index + 1}/3: {wait_state} ({timeout_ms/1000}s)"
                )

                # Create temporary renderer with this strategy
                temp_config = RenderConfig(
                    timeout=timeout_ms / 1000.0,
                    wait_until=wait_state,
                    headless=self.headless,
                    user_agent=self.user_agent,
                    stealth=self.stealth,
                    disable_http2=self.disable_http2,
                    extreme_mode=False,  # Disable to avoid recursion
                    block_resources=self.block_resources,
                    block_images=self.block_images,
                    block_fonts=self.block_fonts,
                    block_media=self.block_media,
                    block_ads=self.block_ads,
                    block_trackers=self.block_trackers,
                    screenshot=self.screenshot,
                )
                temp_renderer = Renderer(config=temp_config)

                # Try rendering with smart waiting
                result = await temp_renderer._render_with_smart_wait(url, timeout_ms)

                # Add metadata about which strategy worked
                result.metadata["extreme_mode"] = True
                result.metadata["strategy_used"] = wait_state
                result.metadata["strategy_attempt"] = strategy_index + 1
                result.metadata["timeout_used_ms"] = timeout_ms

                logger.info(f"[Extreme Mode] Success with {wait_state} strategy")
                return result

            except Exception as e:
                last_exception = e
                error_msg = str(e)
                logger.warning(f"[Extreme Mode] {wait_state} strategy failed: {error_msg[:100]}")

                # If this isn't the last strategy, continue to next
                if strategy_index < len(self.LOAD_STRATEGIES) - 1:
                    continue

        # All strategies failed
        logger.error("[Extreme Mode] All progressive timeout strategies failed")
        raise last_exception

    async def _render_with_smart_wait(self, url: str, timeout_ms: int) -> FetchResult:
        """
        Render with smart content waiting strategies.

        Args:
            url: Target URL to render
            timeout_ms: Timeout in milliseconds

        Returns:
            FetchResult with rendered HTML and metadata
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Install with: "
                "pip install 'markdown-ingress[render]' or pip install playwright && playwright install"
            )

        start_time = time.perf_counter()

        async with async_playwright() as p:
            browser_args = self._prepare_browser_args()
            launch_options = self._prepare_launch_options(browser_args)
            browser = await p.chromium.launch(**launch_options)

            try:
                context_options = self._prepare_context_options()
                context = await browser.new_context(**context_options)

                try:
                    page = await context.new_page()
                    blocker = await self._setup_resource_blocking(page)

                    response = await page.goto(url, timeout=timeout_ms, wait_until=self.wait_until)
                    await self._wait_for_content(page, max_wait=min(30, timeout_ms // 1000))

                    html = await page.content()
                    screenshot_path = await self._capture_screenshot(page)

                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    metadata = self._build_metadata(screenshot_path, blocker)
                    metadata["smart_wait_used"] = True

                    return FetchResult(
                        html=html,
                        url=url,
                        status_code=response.status if response else 200,
                        final_url=page.url,
                        headers=dict(response.headers) if response else {},
                        timing_ms=elapsed_ms,
                        metadata=metadata,
                    )

                finally:
                    await context.close()

            finally:
                await browser.close()

    async def _wait_for_content(self, page, max_wait: int = 30):
        """
        Wait for meaningful content to appear on page.

        Checks for:
        - Body has text content
        - At least one paragraph or content element
        - No "loading" indicators

        Args:
            page: Playwright page object
            max_wait: Maximum seconds to wait
        """
        wait_start = time.time()

        # Try waiting for any content selector
        for selector in self.CONTENT_SELECTORS:
            try:
                await page.wait_for_selector(selector, timeout=min(10000, max_wait * 1000))
                logger.info(f"[Smart Wait] Found content selector: {selector}")
                break
            except Exception:
                continue

        # Wait for body to have meaningful text content
        try:
            await page.wait_for_function(
                """
                () => {
                    const body = document.body;
                    if (!body) return false;

                    // Check for meaningful text content
                    const text = body.innerText || '';
                    if (text.trim().length < 50) return false;

                    // Check for at least one content element
                    const hasContent = document.querySelector('p, article, main, [role="main"]');
                    if (!hasContent) return false;

                    // Check for loading indicators (common patterns)
                    const loadingIndicators = document.querySelectorAll(
                        '[class*="loading"], [class*="spinner"], [id*="loading"]'
                    );
                    for (const indicator of loadingIndicators) {
                        const style = window.getComputedStyle(indicator);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            return false;  // Still loading
                        }
                    }

                    return true;
                }
                """,
                timeout=max(5000, (max_wait - (time.time() - wait_start)) * 1000),
            )
            logger.info("[Smart Wait] Content verification passed")
        except Exception as e:
            # If content check times out, continue anyway
            logger.warning(f"[Smart Wait] Content verification timed out: {e}")
            pass

    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML
        """
        return asyncio.run(self.render(url))
