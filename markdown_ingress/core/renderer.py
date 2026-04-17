"""
Playwright-based renderer for SPA/JavaScript-heavy sites
"""

import asyncio
import atexit
import importlib.util
import logging
import os
import tempfile
import time
from typing import Literal, cast

from markdown_ingress.config_models import RenderConfig
from markdown_ingress.core.renderer_support import (
    _SCREENSHOT_UNSET,
    build_renderer_config,
    execute_render_session,
)
from markdown_ingress.core.resource_blocker import ResourceBlocker
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)

# Track temp screenshots for atexit cleanup to prevent file leaks
_active_screenshots: set[str] = set()


def _cleanup_pending_screenshots() -> None:
    """atexit handler: clean up any temp screenshot files still on disk."""
    for path in list(_active_screenshots):
        try:
            os.unlink(path)
        except OSError:
            pass
    _active_screenshots.clear()


atexit.register(_cleanup_pending_screenshots)

PLAYWRIGHT_INSTALLED = importlib.util.find_spec("playwright") is not None
WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
_RETRYABLE_NAVIGATION_ERRORS = (
    "err_internet_disconnected",
    "err_network_io_suspended",
)

try:
    from markdown_ingress.core.stealth import (
        STEALTH_BROWSER_ARGS,
        get_context_options,
        get_stealth_config,
    )

    STEALTH_AVAILABLE = True
except ImportError:  # pragma: no cover
    STEALTH_AVAILABLE = False  # pragma: no cover

try:  # noqa: I001
    from playwright.async_api import (  # noqa: I001
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
    )
except ImportError:  # pragma: no cover
    PlaywrightError = Exception  # type: ignore[assignment, misc]  # pragma: no cover
    PlaywrightTimeoutError = Exception  # type: ignore[assignment, misc]  # pragma: no cover


class Renderer:  # implements IRenderer protocol
    """Headless browser renderer using Playwright for JavaScript-heavy sites"""

    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "domcontentloaded"  # or "load", "networkidle"

    # Progressive timeout strategies (state, timeout_ms)
    LOAD_STRATEGIES = [
        ("domcontentloaded", 45000),  # Reach parseable DOM quickly
        ("load", 90000),  # Wait for full document load
        ("networkidle", 120000),  # Last resort for chatty SPAs
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
        screenshot: bool | str | None = _SCREENSHOT_UNSET,  # type: ignore[assignment]
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
        config = build_renderer_config(
            self.DEFAULT_WAIT_UNTIL,
            config=config,
            timeout=timeout,
            wait_until=wait_until,
            headless=headless,
            user_agent=user_agent,
            stealth=stealth,
            disable_http2=disable_http2,
            extreme_mode=extreme_mode,
            block_resources=block_resources,
            block_images=block_images,
            block_fonts=block_fonts,
            block_media=block_media,
            block_ads=block_ads,
            block_trackers=block_trackers,
            screenshot=screenshot,
        )

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
            if self._is_retryable_navigation_error(e):
                retry_result = await self._render_with_browser(url)
                retry_result.metadata["navigation_retry"] = True
                retry_result.metadata["original_error"] = error_str[:200]
                return retry_result
            # Check for HTTP/2 protocol error
            if (
                "ERR_HTTP2_PROTOCOL_ERROR" in error_str and not self.disable_http2
            ):  # pragma: no cover
                # Retry with HTTP/2 disabled - create config from current settings
                retry_config = RenderConfig(  # pragma: no cover
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
                result.metadata["http2_fallback"] = True
                result.metadata["original_error"] = "ERR_HTTP2_PROTOCOL_ERROR"
                return result
            # Re-raise if not HTTP/2 error or if already retried
            raise

    @staticmethod
    def _is_retryable_navigation_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(token in message for token in _RETRYABLE_NAVIGATION_ERRORS)

    async def _render_with_browser(self, url: str) -> FetchResult:
        """Internal method to render URL with browser."""
        return await execute_render_session(self, url, self.timeout, smart_wait=False)

    def _prepare_browser_args(self) -> list[str]:
        """Prepare browser launch arguments based on configuration."""
        browser_args = []

        if self.stealth and STEALTH_AVAILABLE:
            browser_args.extend(STEALTH_BROWSER_ARGS)

        if self.disable_http2:
            browser_args.append("--disable-http2")

        return browser_args

    def _prepare_launch_options(self, browser_args: list[str]) -> dict[str, object]:
        """Prepare browser launch options."""
        launch_options: dict[str, object] = {"headless": self.headless}

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
            "bypass_csp": False,
            "ignore_https_errors": False,
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
        """Capture page screenshot if requested.

        Note: When screenshot=True, a temporary file is created. The caller is
        responsible for cleaning up this file when no longer needed. The file
        path is stored in metadata['screenshot_path'] for cleanup tracking.

        Use Renderer.cleanup_screenshot(path) to clean up temp screenshots.
        """
        if not self.screenshot:
            return None

        if self.screenshot is True:
            # Create temp file - tracked for atexit cleanup
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".png", delete=False, prefix="mdingress_screenshot_"
            )
            screenshot_path = temp_file.name
            temp_file.close()
            _active_screenshots.add(screenshot_path)
        else:
            screenshot_path = str(self.screenshot)

        try:
            await page.screenshot(path=screenshot_path, full_page=True)
        except Exception as e:
            logger.debug("Screenshot capture failed: %s", e)
            # Clean up temp file on failure
            if self.screenshot is True:
                Renderer.cleanup_screenshot(screenshot_path)
            raise
        return screenshot_path

    @staticmethod
    def cleanup_screenshot(path: str | None) -> None:
        """Clean up a temporary screenshot file.

        Args:
            path: Path to the screenshot file. If None or empty string, does nothing.
        """
        if not path:
            return
        import os

        try:
            os.unlink(path)
            _active_screenshots.discard(path)
        except OSError:
            _active_screenshots.discard(path)  # File may not exist or already cleaned up

    def _build_metadata(self, screenshot_path: str | None, blocker) -> dict:
        """Build metadata dictionary for the fetch result."""
        metadata = {
            "renderer": "playwright",
            "stealth_mode": self.stealth,
            "http2_disabled": self.disable_http2,
        }

        if screenshot_path:
            metadata["screenshot_path"] = screenshot_path
            # BUG FIX: Mark temp screenshots for cleanup tracking
            if self.screenshot is True:
                metadata["screenshot_temp"] = True

        if blocker:
            stats = blocker.get_stats()
            metadata.update(
                {
                    "resource_blocking": True,
                    "blocked_requests": stats["blocked_requests"],
                    "total_requests": stats["total_requests"],
                    "block_rate_pct": stats["block_rate_pct"],
                    "blocked_by_type": stats["blocked_by_type"],
                }
            )

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
        if last_exception is not None:
            raise last_exception
        # This should not be reachable if LOAD_STRATEGIES is non-empty
        raise RuntimeError(
            "No render strategies configured or all strategies failed without capturing an exception"
        )

    async def _render_with_smart_wait(self, url: str, timeout_ms: int) -> FetchResult:
        """Render with smart content waiting strategies."""
        return await execute_render_session(self, url, timeout_ms, smart_wait=True)

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
        max_wait_ms = max_wait * 1000

        # Try waiting for any content selector, respecting remaining time
        for selector in self.CONTENT_SELECTORS:
            # Calculate remaining time before each selector wait
            elapsed_ms = (time.time() - wait_start) * 1000
            remaining_ms = max(0, max_wait_ms - elapsed_ms)
            # Exit early if no time remains
            if remaining_ms <= 0:
                break
            try:
                # Use remaining time (capped at 2500ms) as timeout
                selector_timeout = min(2500, remaining_ms)
                await page.wait_for_selector(selector, timeout=selector_timeout)
                logger.info(f"[Smart Wait] Found content selector: {selector}")
                break
            except (PlaywrightTimeoutError, PlaywrightError):
                continue

        # Wait for body to have meaningful text content
        # Calculate remaining time, ensuring it's non-negative
        elapsed = time.time() - wait_start
        remaining_ms = max(0, (max_wait - elapsed)) * 1000
        # If no time remaining, skip the content verification wait
        if remaining_ms <= 0:
            logger.warning("[Smart Wait] No time remaining for content verification")
            return
        # Use remaining time (capped at 3000ms) as timeout
        timeout_ms = min(3000, int(remaining_ms))

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
                timeout=timeout_ms,
            )
            logger.info("[Smart Wait] Content verification passed")
        except Exception as e:
            # If content check times out, continue anyway
            logger.warning(f"[Smart Wait] Content verification timed out: {e}")
            pass

    async def _navigate_page(self, page, url: str, timeout_ms: int):
        """Navigate with a pragmatic load strategy that prefers early usable DOM."""
        response = await page.goto(
            url,
            timeout=timeout_ms,
            wait_until=cast(WaitUntil, self.wait_until),
        )
        if self.wait_until != "networkidle":
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=min(1500, timeout_ms))
            except Exception as e:
                logger.debug("DOMContentLoaded wait skipped: %s", e)
        return response

    async def _extract_page_content(self, page) -> str:
        """Read page HTML while tolerating transient navigation churn."""
        for attempt in range(3):
            try:
                return cast(str, await page.content())
            except Exception as exc:
                if "page is navigating" not in str(exc).lower():
                    raise
                if attempt == 2:
                    break
                await asyncio.sleep(0.2 * (attempt + 1))

        # Fallback: use page.evaluate after loop exhaustion
        html = None  # BUG FIX: Initialize to prevent NameError on exception
        try:
            html = await page.evaluate("""
                () => {
                    const root = document.documentElement;
                    return root ? root.outerHTML : '';
                }
                """)
            if isinstance(html, str) and html.strip():
                return html
        except Exception as e:
            # Last resort fallback - log and re-raise original error
            logger.debug("Page evaluate fallback failed: %s", e)
            pass
        raise RuntimeError("Unable to retrieve page content after navigation churn")

    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.render(url))

        raise RuntimeError(
            "render_sync() cannot run inside an active event loop; await render() instead"
        )
