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

StealthSignature = tuple[str, tuple[int, int], str, float]


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


def _signature_for_config(config: "AdvancedStealthConfig") -> StealthSignature:
    """Return a stable signature representing the fingerprint-relevant config."""
    return (
        config.user_agent,
        (config.viewport_width, config.viewport_height),
        config.timezone,
        config.device_scale_factor,
    )


class StealthConfigGenerator:
    """Generate stealth configs while tracking previous signature per request.

    Instances intentionally carry state, allowing callers to avoid immediate
    repetition without sharing state globally.
    """

    def __init__(
        self,
        randomize: bool = True,
        user_agent: str | None = None,
        viewport: tuple[int, int] | None = None,
        timezone: str | None = None,
    ):
        self.randomize = randomize
        self.user_agent = user_agent
        self.viewport = viewport
        self.timezone = timezone
        self._previous_signature: StealthSignature | None = None

    def next_config(self) -> AdvancedStealthConfig:
        """Return next config and remember its signature for request-scoped dedupe."""
        config = get_advanced_stealth_config(
            randomize=self.randomize,
            user_agent=self.user_agent,
            viewport=self.viewport,
            timezone=self.timezone,
            previous_signature=self._previous_signature,
        )
        self._previous_signature = _signature_for_config(config)
        return config


__all__ = [
    "StealthConfigGenerator",
    "StealthSignature",
    "_signature_for_config",
]


def get_advanced_stealth_config(
    randomize: bool = True,
    user_agent: str | None = None,
    viewport: tuple[int, int] | None = None,
    timezone: str | None = None,
    previous_signature: StealthSignature | None = None,
) -> AdvancedStealthConfig:
    """
    Get an advanced stealth configuration with maximum anti-detection.

    Args:
        randomize: Whether to randomize user agent and viewport
        user_agent: Custom user agent (overrides randomization)
        viewport: Custom viewport as (width, height) tuple
        timezone: Custom timezone (e.g., "America/New_York")
        previous_signature: Optional signature to avoid immediate repetition

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

        signature = (
            selected_ua,
            selected_viewport,
            selected_timezone,
            device_scale_factor,
        )

        # Keep regenerating until we get a unique signature (max 10 attempts)
        attempts = 0
        while signature == previous_signature and attempts < 10:
            # Prefer changing UA first, then viewport, then timezone, then scale
            if (
                user_agent is None
                and selected_ua in ADVANCED_USER_AGENTS
                and len(ADVANCED_USER_AGENTS) > 1
            ):
                ua_index = (ADVANCED_USER_AGENTS.index(selected_ua) + 1) % len(ADVANCED_USER_AGENTS)
                selected_ua = ADVANCED_USER_AGENTS[ua_index]
            elif (
                viewport is None
                and selected_viewport in ADVANCED_VIEWPORT_SIZES
                and len(ADVANCED_VIEWPORT_SIZES) > 1
            ):
                viewport_index = (ADVANCED_VIEWPORT_SIZES.index(selected_viewport) + 1) % len(
                    ADVANCED_VIEWPORT_SIZES
                )
                selected_viewport = ADVANCED_VIEWPORT_SIZES[viewport_index]
            elif timezone is None and selected_timezone in TIMEZONES and len(TIMEZONES) > 1:
                timezone_index = (TIMEZONES.index(selected_timezone) + 1) % len(TIMEZONES)
                selected_timezone = TIMEZONES[timezone_index]
            else:
                # Use a pool of realistic scale factors for diversity
                realistic_scales = [1.0, 1.25, 1.5, 1.75, 2.0]
                available_scales = [s for s in realistic_scales if s != device_scale_factor]
                if available_scales:
                    scale_choice = _rng.choice(available_scales)
                else:
                    scale_choice = 1.5
                device_scale_factor = scale_choice

            signature = (
                selected_ua,
                selected_viewport,
                selected_timezone,
                device_scale_factor,
            )
            attempts += 1
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
