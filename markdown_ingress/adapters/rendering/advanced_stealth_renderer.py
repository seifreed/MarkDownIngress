"""Advanced stealth Playwright renderer — implements IRenderer protocol."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TypedDict, Unpack, cast

import markdown_ingress.config_validation as config_validation
from markdown_ingress.adapters.rendering.renderer_navigation import WaitUntil
from markdown_ingress.adapters.rendering.renderer_support import (
    SharedRendererMixin,
    _close_async_resource,
    import_async_playwright,
    launch_chromium,
    raise_for_render_status,
    timeout_seconds_to_ms,
)
from markdown_ingress.core.ssrf import resolve_allow_local_urls
from markdown_ingress.core.stealth import (
    AdvancedStealthConfig,
    get_advanced_context_options,
    get_advanced_stealth_config,
    inject_stealth_post_nav,
    inject_stealth_pre_nav,
)
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)
VALID_WAIT_UNTIL = config_validation.VALID_WAIT_UNTIL
_ensure_bool = config_validation.ensure_bool
_ensure_finite_float = config_validation.ensure_finite_float
_ensure_optional_bool = config_validation.ensure_optional_bool
_ensure_str = config_validation.ensure_str


class AdvancedStealthRendererOptions(TypedDict, total=False):
    timeout: float
    wait_until: str
    headless: bool
    randomize_fingerprint: bool
    disable_http2: bool
    stealth_config: AdvancedStealthConfig | None
    allow_local_urls: bool | None
    block_resources: bool
    block_images: bool
    block_fonts: bool
    block_media: bool
    block_ads: bool
    block_trackers: bool


_ADVANCED_STEALTH_OPTION_NAMES = (
    "timeout",
    "wait_until",
    "headless",
    "randomize_fingerprint",
    "disable_http2",
    "stealth_config",
    "allow_local_urls",
    "block_resources",
    "block_images",
    "block_fonts",
    "block_media",
    "block_ads",
    "block_trackers",
)
_ADVANCED_STEALTH_POSITIONAL_NAMES = _ADVANCED_STEALTH_OPTION_NAMES[:6]
_ADVANCED_STEALTH_OPTION_NAME_SET = frozenset(_ADVANCED_STEALTH_OPTION_NAMES)


def _normalize_advanced_stealth_options(
    args: tuple[object, ...],
    options: AdvancedStealthRendererOptions,
) -> AdvancedStealthRendererOptions:
    if len(args) > len(_ADVANCED_STEALTH_POSITIONAL_NAMES):
        raise TypeError(
            f"AdvancedStealthRenderer() expected at most "
            f"{len(_ADVANCED_STEALTH_POSITIONAL_NAMES)} arguments"
        )

    unexpected = set(options) - _ADVANCED_STEALTH_OPTION_NAME_SET
    if unexpected:
        name = sorted(unexpected)[0]
        raise TypeError(f"AdvancedStealthRenderer() got an unexpected keyword argument '{name}'")

    normalized = dict(options)
    for index, value in enumerate(args):
        name = _ADVANCED_STEALTH_POSITIONAL_NAMES[index]
        if name in normalized:
            raise TypeError(f"AdvancedStealthRenderer() got multiple values for argument '{name}'")
        normalized[name] = value
    return cast(AdvancedStealthRendererOptions, normalized)


def _advanced_stealth_renderer_from_options(
    args: tuple[object, ...],
    options: dict[str, Any],
) -> AdvancedStealthRenderer:
    if len(args) > 2:
        raise TypeError("expected at most 2 positional arguments after url")
    parsed = dict(options)
    positional_names = ("timeout", "headless")
    for index, value in enumerate(args):
        name = positional_names[index]
        if name in parsed:
            raise TypeError(f"got multiple values for argument '{name}'")
        parsed[name] = value
    return AdvancedStealthRenderer(**parsed)


class AdvancedStealthRenderer(SharedRendererMixin):
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
        *args: object,
        **options: Unpack[AdvancedStealthRendererOptions],
    ) -> None:
        parsed = _normalize_advanced_stealth_options(args, options)
        timeout = parsed.get("timeout", 30.0)
        wait_until = parsed.get("wait_until", "networkidle")
        headless = parsed.get("headless", True)
        randomize_fingerprint = parsed.get("randomize_fingerprint", True)
        disable_http2 = parsed.get("disable_http2", False)
        stealth_config = parsed.get("stealth_config")
        allow_local_urls = parsed.get("allow_local_urls")
        block_resources = parsed.get("block_resources", True)
        block_images = parsed.get("block_images", True)
        block_fonts = parsed.get("block_fonts", True)
        block_media = parsed.get("block_media", True)
        block_ads = parsed.get("block_ads", True)
        block_trackers = parsed.get("block_trackers", True)

        timeout = _ensure_finite_float("timeout", timeout)
        if timeout <= 0.0:
            raise ValueError(f"timeout must be > 0.0, got {timeout}")
        wait_until = _ensure_str("wait_until", wait_until)
        if wait_until not in VALID_WAIT_UNTIL:
            raise ValueError(
                f"Invalid wait_until '{wait_until}'. Must be one of: "
                f"{', '.join(VALID_WAIT_UNTIL)}"
            )

        self.timeout = timeout_seconds_to_ms(timeout)
        self.wait_until = wait_until
        self.headless = _ensure_bool("headless", headless)
        self.randomize_fingerprint = _ensure_bool("randomize_fingerprint", randomize_fingerprint)
        self.disable_http2 = _ensure_bool("disable_http2", disable_http2)
        self.allow_local_urls = resolve_allow_local_urls(
            _ensure_optional_bool("allow_local_urls", allow_local_urls)
        )
        self.block_resources = _ensure_bool("block_resources", block_resources)
        self.block_images = _ensure_bool("block_images", block_images)
        self.block_fonts = _ensure_bool("block_fonts", block_fonts)
        self.block_media = _ensure_bool("block_media", block_media)
        self.block_ads = _ensure_bool("block_ads", block_ads)
        self.block_trackers = _ensure_bool("block_trackers", block_trackers)
        self._base_dns_pins: dict[str, str] = {}
        self._dns_pins: dict[str, str] = {}

        if stealth_config is None:
            self.stealth_config = get_advanced_stealth_config(randomize=randomize_fingerprint)
        else:
            self.stealth_config = stealth_config

    async def render(self, url: str) -> FetchResult:
        """
        Render URL using advanced stealth techniques.

        Includes automatic retry logic and HTTP/2 fallback.
        """
        validated_url = self._validate_render_url(url)
        try:
            return await self._render_with_browser(validated_url)
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
                    **self._block_settings(),
                )
                retry_renderer._dns_pins = dict(self._dns_pins)
                result = await retry_renderer._render_with_browser(validated_url)
                result.metadata["http2_fallback"] = True
                result.metadata["original_error"] = "ERR_HTTP2_PROTOCOL_ERROR"
                return result
            raise

    async def _render_with_browser(self, url: str) -> FetchResult:
        """Internal method to render URL with browser."""
        async_playwright = import_async_playwright()

        start_time = time.perf_counter()

        async with async_playwright() as p:
            browser_args = self.stealth_config.browser_args.copy()

            self._append_network_browser_args(browser_args)

            launch_options: dict[str, object] = {
                "headless": self.headless,
                "args": browser_args,
                "ignore_default_args": ["--enable-automation"],
            }

            browser = None
            browser = await launch_chromium(p.chromium, launch_options, self.timeout)
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
                    raise_for_render_status(response, url)

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
                        "viewport": (
                            f"{self.stealth_config.viewport_width}x"
                            f"{self.stealth_config.viewport_height}"
                        ),
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


# Compatibility helper accepts AdvancedStealthRenderer keyword options.
async def render_with_advanced_stealth(
    url: str,
    *args: object,
    **options: Any,
) -> FetchResult:
    """Convenience function to render a URL with advanced stealth."""
    renderer = _advanced_stealth_renderer_from_options(args, options)
    return await renderer.render(url)


# Synchronous compatibility helper mirrors the async helper signature.
def render_with_advanced_stealth_sync(
    url: str,
    *args: object,
    **options: Any,
) -> FetchResult:
    """Synchronous convenience function for advanced stealth rendering."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(render_with_advanced_stealth(url, *args, **options))

    raise RuntimeError(
        "render_with_advanced_stealth_sync() cannot run inside an active event loop; "
        "await render_with_advanced_stealth() instead"
    )
