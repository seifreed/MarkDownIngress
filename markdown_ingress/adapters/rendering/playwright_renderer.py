"""Playwright-based renderer adapter for SPA/JavaScript-heavy sites."""

import asyncio
import importlib.util
import logging

from markdown_ingress.adapters.rendering.browser_dns import chromium_host_resolver_rules
from markdown_ingress.adapters.rendering.renderer_navigation import (
    CONTENT_SELECTORS as DEFAULT_CONTENT_SELECTORS,
)
from markdown_ingress.adapters.rendering.renderer_navigation import (
    extract_page_content,
    navigate_page,
    wait_for_content,
)
from markdown_ingress.adapters.rendering.renderer_screenshots import (
    capture_screenshot,
)
from markdown_ingress.adapters.rendering.renderer_screenshots import (
    cleanup_screenshot as cleanup_screenshot_file,
)
from markdown_ingress.adapters.rendering.renderer_support import (
    _SCREENSHOT_UNSET,
    RendererConfigInputs,
    build_renderer_config,
    execute_render_session,
)
from markdown_ingress.config_models import RenderConfig
from markdown_ingress.core.interfaces import IRenderer
from markdown_ingress.core.resource_blocker import ResourceBlocker
from markdown_ingress.core.ssrf import (
    dns_pin_for_validated_http_url,
    resolve_allow_local_urls,
    validate_http_url_no_ssrf_with_dns_check,
)
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)

PLAYWRIGHT_INSTALLED = importlib.util.find_spec("playwright") is not None
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


class Renderer(IRenderer):
    """Headless browser renderer using Playwright for JavaScript-heavy sites."""

    DEFAULT_TIMEOUT = 30000
    DEFAULT_WAIT_UNTIL = "domcontentloaded"

    LOAD_STRATEGIES = (
        ("domcontentloaded", 45000),
        ("load", 90000),
        ("networkidle", 120000),
    )

    CONTENT_SELECTORS = DEFAULT_CONTENT_SELECTORS

    # Public renderer constructor accepts legacy kwargs before normalizing config.
    def __init__(  # noqa: PLR0913
        self,
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
        screenshot: bool | str | None = _SCREENSHOT_UNSET,
        allow_local_urls: bool | None = None,
    ):
        config = build_renderer_config(
            self.DEFAULT_WAIT_UNTIL,
            RendererConfigInputs(
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
                allow_local_urls=allow_local_urls,
            ),
        )

        self.timeout = int(config.timeout * 1000)
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
        self.allow_local_urls = config.allow_local_urls
        self._base_dns_pins = dict(config.dns_pins)
        self._dns_pins = dict(self._base_dns_pins)

    def _validate_render_url(self, url: str) -> str:
        logical_url = str(url).strip()
        validated_url = validate_http_url_no_ssrf_with_dns_check(
            logical_url,
            allow_local=resolve_allow_local_urls(self.allow_local_urls),
        )
        self._dns_pins = dict(self._base_dns_pins)
        pin = dns_pin_for_validated_http_url(logical_url, validated_url)
        if pin is not None:
            self._dns_pins[pin[0]] = pin[1]
        return logical_url

    async def render(self, url: str) -> FetchResult:
        validated_url = self._validate_render_url(url)
        if self.extreme_mode:
            return await self._render_with_progressive_timeout(validated_url)

        try:
            return await self._render_with_browser(validated_url)
        except Exception as e:
            error_str = str(e)
            if self._is_retryable_navigation_error(e):
                retry_result = await self._render_with_browser(validated_url)
                retry_result.metadata["navigation_retry"] = True
                retry_result.metadata["original_error"] = error_str[:200]
                return retry_result
            if (
                "ERR_HTTP2_PROTOCOL_ERROR" in error_str and not self.disable_http2
            ):  # pragma: no cover
                retry_config = RenderConfig(  # pragma: no cover
                    timeout=self.timeout / 1000.0,
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
                    allow_local_urls=self.allow_local_urls,
                    dns_pins=dict(self._dns_pins),
                )
                retry_renderer = Renderer(config=retry_config)
                retry_renderer._base_dns_pins = dict(self._dns_pins)
                retry_renderer._dns_pins = dict(self._dns_pins)
                result = await retry_renderer._render_with_browser(validated_url)
                result.metadata["http2_fallback"] = True
                result.metadata["original_error"] = "ERR_HTTP2_PROTOCOL_ERROR"
                return result
            raise

    @staticmethod
    def _is_retryable_navigation_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(token in message for token in _RETRYABLE_NAVIGATION_ERRORS)

    async def _render_with_browser(self, url: str) -> FetchResult:
        return await execute_render_session(self, url, self.timeout, smart_wait=False)

    def _prepare_browser_args(self) -> list[str]:
        browser_args = []
        if self.stealth and STEALTH_AVAILABLE:
            browser_args.extend(STEALTH_BROWSER_ARGS)
        if self.disable_http2:
            browser_args.append("--disable-http2")
        resolver_rules = chromium_host_resolver_rules(self._dns_pins)
        if resolver_rules:
            browser_args.append(f"--host-resolver-rules={resolver_rules}")
        return browser_args

    def _prepare_launch_options(self, browser_args: list[str]) -> dict[str, object]:
        launch_options: dict[str, object] = {"headless": self.headless}
        if browser_args:
            launch_options["args"] = browser_args
            launch_options["ignore_default_args"] = ["--enable-automation"]
        return launch_options

    def _prepare_context_options(self) -> dict:
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
            dns_pins=self._dns_pins,
            enforce_dns_pinning=True,
        )
        await blocker.setup_blocking(page)
        return blocker

    async def _capture_screenshot(self, page) -> str | None:
        return await capture_screenshot(page, self.screenshot)

    @staticmethod
    def cleanup_screenshot(path: str | None) -> None:
        cleanup_screenshot_file(path)

    def _build_metadata(self, screenshot_path: str | None, blocker) -> dict:
        metadata = {
            "renderer": "playwright",
            "stealth_mode": self.stealth,
            "http2_disabled": self.disable_http2,
        }

        if screenshot_path:
            metadata["screenshot_path"] = screenshot_path
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
        last_exception = None

        for strategy_index, (wait_state, timeout_ms) in enumerate(self.LOAD_STRATEGIES):
            try:
                logger.info(
                    f"[Extreme Mode] Attempt {strategy_index + 1}/3: "
                    f"{wait_state} ({timeout_ms / 1000}s)"
                )

                temp_config = RenderConfig(
                    timeout=timeout_ms / 1000.0,
                    wait_until=wait_state,
                    headless=self.headless,
                    user_agent=self.user_agent,
                    stealth=self.stealth,
                    disable_http2=self.disable_http2,
                    extreme_mode=False,
                    block_resources=self.block_resources,
                    block_images=self.block_images,
                    block_fonts=self.block_fonts,
                    block_media=self.block_media,
                    block_ads=self.block_ads,
                    block_trackers=self.block_trackers,
                    screenshot=self.screenshot,
                    allow_local_urls=self.allow_local_urls,
                    dns_pins=dict(self._dns_pins),
                )
                temp_renderer = Renderer(config=temp_config)
                result = await temp_renderer._render_with_smart_wait(url, timeout_ms)
                result.metadata["extreme_mode"] = True
                result.metadata["strategy_used"] = wait_state
                result.metadata["strategy_attempt"] = strategy_index + 1
                result.metadata["timeout_used_ms"] = timeout_ms
                logger.info(f"[Extreme Mode] Success with {wait_state} strategy")

            except Exception as e:
                last_exception = e
                error_msg = str(e)
                logger.warning(f"[Extreme Mode] {wait_state} strategy failed: {error_msg[:100]}")
                if strategy_index < len(self.LOAD_STRATEGIES) - 1:
                    continue
            else:
                return result

        logger.error("[Extreme Mode] All progressive timeout strategies failed")
        if last_exception is not None:
            raise last_exception
        raise RuntimeError(
            "No render strategies configured or all strategies failed without "
            "capturing an exception"
        )

    async def _render_with_smart_wait(self, url: str, timeout_ms: int) -> FetchResult:
        return await execute_render_session(self, url, timeout_ms, smart_wait=True)

    async def _wait_for_content(self, page, max_wait: int = 30):
        await wait_for_content(page, max_wait=max_wait, selectors=self.CONTENT_SELECTORS)

    async def _navigate_page(self, page, url: str, timeout_ms: int):
        return await navigate_page(page, url, timeout_ms, wait_until=self.wait_until)

    async def _extract_page_content(self, page) -> str:
        return await extract_page_content(page)

    def render_sync(self, url: str) -> FetchResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.render(url))

        raise RuntimeError(
            "render_sync() cannot run inside an active event loop; await render() instead"
        )


# Re-export for backward compatibility with adapters/rendering/playwright_renderer.py importers
PLAYWRIGHT_AVAILABLE = PLAYWRIGHT_INSTALLED
PlaywrightRenderer = Renderer
