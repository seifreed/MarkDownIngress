"""
Browser configuration for stealth mode.

This module provides:
- User agent pools
- Viewport sizes
- Browser launch arguments
- HTTP headers
- Configuration dataclass
"""

import random
from dataclasses import dataclass, field
from typing import Any

from markdown_ingress.core.stealth.browser_profiles import (
    ADVANCED_USER_AGENTS,
    ADVANCED_VIEWPORT_SIZES,
    REALISTIC_HEADERS,
    TIMEZONES,
    ULTRA_STEALTH_ARGS,
)
from markdown_ingress.core.stealth.browser_profiles import (
    build_client_hints as _build_client_hints,
)

_rng = random.SystemRandom()


# ============================================================================
# CONFIGURATION DATACLASS
# ============================================================================


@dataclass
class AdvancedStealthConfig:
    """Advanced stealth configuration with full customization."""

    user_agent: str
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    locale: str = "en-US"
    timezone: str = "America/New_York"
    permissions: list[str] = field(default_factory=lambda: ["geolocation", "notifications"])
    extra_http_headers: dict[str, str] = field(default_factory=dict)
    browser_args: list[str] = field(default_factory=list)

    # Advanced features
    enable_javascript: bool = True
    bypass_csp: bool = True
    ignore_https_errors: bool = True
    has_touch: bool = False
    is_mobile: bool = False
    geolocation: dict | None = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def get_advanced_stealth_config(
    randomize: bool = True,
    user_agent: str | None = None,
    viewport: tuple[int, int] | None = None,
    timezone: str | None = None,
) -> AdvancedStealthConfig:
    """
    Get an advanced stealth configuration with maximum anti-detection.

    Args:
        randomize: Whether to randomize user agent and viewport
        user_agent: Custom user agent (overrides randomization)
        viewport: Custom viewport as (width, height) tuple
        timezone: Custom timezone (e.g., "America/New_York")

    Returns:
        AdvancedStealthConfig: Comprehensive stealth configuration

    Example:
        >>> print(config.user_agent)
        Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...
        >>> print(config.viewport_width, config.viewport_height)
        1920 1080
    """
    if randomize:
        selected_ua = user_agent or _rng.choice(ADVANCED_USER_AGENTS)
        selected_viewport = viewport or _rng.choice(ADVANCED_VIEWPORT_SIZES)
        selected_timezone = timezone or _rng.choice(TIMEZONES)
        device_scale_factor = round(_rng.uniform(1.0, 2.0), 2)
    else:
        selected_ua = user_agent or ADVANCED_USER_AGENTS[0]
        selected_viewport = viewport or ADVANCED_VIEWPORT_SIZES[0]
        selected_timezone = timezone or TIMEZONES[0]
        device_scale_factor = 1.0

    viewport_width, viewport_height = selected_viewport

    return AdvancedStealthConfig(
        user_agent=selected_ua,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        device_scale_factor=device_scale_factor,
        locale="en-US",
        timezone=selected_timezone,
        permissions=["geolocation", "notifications"],
        extra_http_headers={**REALISTIC_HEADERS, **_build_client_hints(selected_ua)},
        browser_args=ULTRA_STEALTH_ARGS.copy(),
    )


def get_advanced_context_options(
    stealth_config: AdvancedStealthConfig | None = None,
) -> dict[str, Any]:
    """
    Get browser context options with all anti-detection measures enabled.

    Args:
        stealth_config: Optional AdvancedStealthConfig. If not provided,
                       a randomized one is generated.

    Returns:
        dict: Context options suitable for playwright's browser.new_context()
              with comprehensive stealth settings.

    Example:
        >>> options = get_advanced_context_options()
        >>> context = await browser.new_context(**options)
    """
    if stealth_config is None:
        stealth_config = get_advanced_stealth_config()

    context_options = {
        "user_agent": stealth_config.user_agent,
        "viewport": {
            "width": stealth_config.viewport_width,
            "height": stealth_config.viewport_height,
        },
        "device_scale_factor": stealth_config.device_scale_factor,
        "locale": stealth_config.locale,
        "timezone_id": stealth_config.timezone,
        "bypass_csp": stealth_config.bypass_csp,
        "ignore_https_errors": stealth_config.ignore_https_errors,
        "java_script_enabled": stealth_config.enable_javascript,
        "has_touch": stealth_config.has_touch,
        "is_mobile": stealth_config.is_mobile,
        "extra_http_headers": stealth_config.extra_http_headers,
    }

    # Add permissions
    if stealth_config.permissions:
        context_options["permissions"] = stealth_config.permissions

    # Add geolocation if specified
    if stealth_config.geolocation:
        context_options["geolocation"] = stealth_config.geolocation

    return context_options
