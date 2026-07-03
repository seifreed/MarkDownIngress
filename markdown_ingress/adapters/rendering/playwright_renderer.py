"""Playwright-based renderer adapter for SPA/JavaScript-heavy sites."""

import logging
from typing import Any, TypedDict, Unpack, cast

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
    SharedRendererMixin,
    build_renderer_config,
    execute_render_session,
    timeout_seconds_to_ms,
)
from markdown_ingress.config_models import RenderConfig
from markdown_ingress.config_validation import collect_option_values
from markdown_ingress.core.interfaces import IRenderer
from markdown_ingress.models import FetchResult
from markdown_ingress.runtime_helpers import is_dependency_available, load_optional_object

logger = logging.getLogger(__name__)

PLAYWRIGHT_INSTALLED = is_dependency_available("playwright")

_PLAYWRIGHT_ERROR: type[Exception] = RuntimeError
if PLAYWRIGHT_INSTALLED:
    try:
        candidate = load_optional_object(
            "playwright.async_api", "Error", purpose="playwright rendering"
        )
        if isinstance(candidate, type) and issubclass(candidate, Exception):
            _PLAYWRIGHT_ERROR = cast(type[Exception], candidate)
    except ImportError:
        _PLAYWRIGHT_ERROR = RuntimeError
_RETRYABLE_NAVIGATION_ERRORS = (
    "err_internet_disconnected",
    "err_network_io_suspended",
)

_RENDERER_EXECUTION_ERRORS = (RuntimeError, TimeoutError, OSError, _PLAYWRIGHT_ERROR)


_PROGRESSIVE_RENDER_ERRORS = (
    OSError,
    RuntimeError,
    TimeoutError,
    _PLAYWRIGHT_ERROR,
)


class RendererOptions(TypedDict, total=False):
    config: RenderConfig | None
    timeout: float | None
    wait_until: str | None
    headless: bool | None
    user_agent: str | None
    stealth: bool | None
    disable_http2: bool | None
    extreme_mode: bool | None
    block_resources: bool | None
    block_images: bool | None
    block_fonts: bool | None
    block_media: bool | None
    block_ads: bool | None
    block_trackers: bool | None
    screenshot: bool | str | None
    allow_local_urls: bool | None


_RENDERER_OPTION_NAMES = (
    "config",
    "timeout",
    "wait_until",
    "headless",
    "user_agent",
    "stealth",
    "disable_http2",
    "extreme_mode",
    "block_resources",
    "block_images",
    "block_fonts",
    "block_media",
    "block_ads",
    "block_trackers",
    "screenshot",
    "allow_local_urls",
)


def _normalize_renderer_inputs(
    args: tuple[object, ...],
    options: RendererOptions,
) -> RendererConfigInputs:
    parsed = cast(
        dict[str, Any],
        collect_option_values("Renderer()", _RENDERER_OPTION_NAMES, args, options),
    )

    return RendererConfigInputs(
        config=parsed.get("config"),
        timeout=parsed.get("timeout"),
        wait_until=parsed.get("wait_until"),
        headless=parsed.get("headless"),
        user_agent=parsed.get("user_agent"),
        stealth=parsed.get("stealth"),
        disable_http2=parsed.get("disable_http2"),
        extreme_mode=parsed.get("extreme_mode"),
        block_resources=parsed.get("block_resources"),
        block_images=parsed.get("block_images"),
        block_fonts=parsed.get("block_fonts"),
        block_media=parsed.get("block_media"),
        block_ads=parsed.get("block_ads"),
        block_trackers=parsed.get("block_trackers"),
        screenshot=parsed.get("screenshot", _SCREENSHOT_UNSET),
        allow_local_urls=parsed.get("allow_local_urls"),
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


class Renderer(SharedRendererMixin, IRenderer):
    """Headless browser renderer using Playwright for JavaScript-heavy sites."""

    DEFAULT_TIMEOUT = 30000
    DEFAULT_WAIT_UNTIL = "domcontentloaded"

    LOAD_STRATEGIES = (
        ("domcontentloaded", 45000),
        ("load", 90000),
        ("networkidle", 120000),
    )

    CONTENT_SELECTORS = DEFAULT_CONTENT_SELECTORS

    def __init__(self, *args: object, **options: Unpack[RendererOptions]) -> None:
        config = build_renderer_config(
            self.DEFAULT_WAIT_UNTIL,
            _normalize_renderer_inputs(args, options),
        )

        self.timeout = timeout_seconds_to_ms(config.timeout)
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

    async def render(self, url: str) -> FetchResult:
        validated_url = self._validate_render_url(url)
        if self.extreme_mode:
            return await self._render_with_progressive_timeout(validated_url)

        try:
            return await self._render_with_browser(validated_url)
        except _RENDERER_EXECUTION_ERRORS as e:
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
                    **self._block_settings(),
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
    def _is_retryable_navigation_error(exc: BaseException) -> bool:
        message = str(exc).lower()
        return any(token in message for token in _RETRYABLE_NAVIGATION_ERRORS)

    async def _render_with_browser(self, url: str) -> FetchResult:
        return await execute_render_session(self, url, self.timeout, smart_wait=False)

    def _prepare_browser_args(self) -> list[str]:
        browser_args = []
        if self.stealth and STEALTH_AVAILABLE:
            browser_args.extend(STEALTH_BROWSER_ARGS)
        self._append_network_browser_args(browser_args)
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
                    "resource_blocking": self.block_resources,
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
                    **self._block_settings(),
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

            except _PROGRESSIVE_RENDER_ERRORS as e:
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


# Re-export for backward compatibility with adapters/rendering/playwright_renderer.py importers
PLAYWRIGHT_AVAILABLE = PLAYWRIGHT_INSTALLED
PlaywrightRenderer = Renderer
