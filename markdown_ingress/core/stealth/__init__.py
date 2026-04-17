"""
Stealth submodules for advanced bot detection evasion.

This package provides comprehensive stealth capabilities including:
- Browser configuration and user agents
- JavaScript injection for detection patches
- Fingerprint spoofing (WebGL, Canvas)
- Advanced stealth renderer

Public API exports maintain backward compatibility with advanced_stealth.py
"""

from dataclasses import dataclass

# Browser configuration
from .browser_config import (
    ADVANCED_USER_AGENTS,
    ADVANCED_VIEWPORT_SIZES,
    REALISTIC_HEADERS,
    TIMEZONES,
    ULTRA_STEALTH_ARGS,
    AdvancedStealthConfig,
    StealthConfigGenerator,
    get_advanced_context_options,
    get_advanced_stealth_config,
)

# Fingerprinting
from .fingerprint import (
    CANVAS_FINGERPRINT_JS,
    WEBGL_FINGERPRINT_JS,
)

# JavaScript injection
from .js_injection import (
    STEALTH_JS_INJECTION,
    STEALTH_JS_POST_LOAD,
    inject_stealth,
    inject_stealth_post_nav,
    inject_stealth_pre_nav,
)

# Backward-compatible aliases used by renderer.py legacy imports.
STEALTH_BROWSER_ARGS = ULTRA_STEALTH_ARGS


@dataclass
class StealthConfig:
    """Backward-compatible stealth config for legacy renderer imports."""

    user_agent: str
    viewport_width: int
    viewport_height: int
    locale: str = "en-US"
    timezone: str = "America/New_York"
    browser_args: list[str] | None = None


def get_stealth_config() -> StealthConfig:
    """Legacy wrapper around advanced stealth config."""
    config = get_advanced_stealth_config()
    return StealthConfig(
        user_agent=config.user_agent,
        viewport_width=config.viewport_width,
        viewport_height=config.viewport_height,
        locale=config.locale,
        timezone=config.timezone,
        browser_args=list(ULTRA_STEALTH_ARGS),
    )


def get_context_options(config: StealthConfig | None = None) -> dict:
    """Legacy wrapper around advanced context options."""
    if config is None:
        return get_advanced_context_options()
    return {
        "user_agent": config.user_agent,
        "viewport": {"width": config.viewport_width, "height": config.viewport_height},
        "locale": config.locale,
        "timezone_id": config.timezone,
    }


__all__ = [
    # Legacy compatibility
    "StealthConfig",
    "STEALTH_BROWSER_ARGS",
    "get_stealth_config",
    "get_context_options",
    # Config dataclass
    "AdvancedStealthConfig",
    "StealthConfigGenerator",
    # Constants - Browser config
    "ULTRA_STEALTH_ARGS",
    "ADVANCED_USER_AGENTS",
    "ADVANCED_VIEWPORT_SIZES",
    "TIMEZONES",
    "REALISTIC_HEADERS",
    # Constants - JavaScript injection
    "STEALTH_JS_INJECTION",
    "STEALTH_JS_POST_LOAD",
    # Constants - Fingerprinting
    "WEBGL_FINGERPRINT_JS",
    "CANVAS_FINGERPRINT_JS",
    # Functions - Browser config
    "get_advanced_stealth_config",
    "get_advanced_context_options",
    # Functions - JavaScript injection
    "inject_stealth",
    "inject_stealth_pre_nav",
    "inject_stealth_post_nav",
]
