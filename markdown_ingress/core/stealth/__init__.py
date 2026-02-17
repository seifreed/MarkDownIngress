"""
Stealth submodules for advanced bot detection evasion.

This package provides comprehensive stealth capabilities including:
- Browser configuration and user agents
- JavaScript injection for detection patches
- Fingerprint spoofing (WebGL, Canvas)
- Advanced stealth renderer

Public API exports maintain backward compatibility with advanced_stealth.py
"""

# Browser configuration
from .browser_config import (
    ADVANCED_USER_AGENTS,
    ADVANCED_VIEWPORT_SIZES,
    AdvancedStealthConfig,
    REALISTIC_HEADERS,
    TIMEZONES,
    ULTRA_STEALTH_ARGS,
    get_advanced_context_options,
    get_advanced_stealth_config,
)

# JavaScript injection
from .js_injection import (
    STEALTH_JS_INJECTION,
    STEALTH_JS_POST_LOAD,
    inject_stealth,
)

# Fingerprinting
from .fingerprint import (
    CANVAS_FINGERPRINT_JS,
    WEBGL_FINGERPRINT_JS,
)

__all__ = [
    # Config dataclass
    "AdvancedStealthConfig",
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
]
