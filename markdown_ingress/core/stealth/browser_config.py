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
import re
from dataclasses import dataclass, field
from typing import Any

_rng = random.SystemRandom()

# ============================================================================
# ULTRA STEALTH BROWSER LAUNCH ARGUMENTS
# ============================================================================

ULTRA_STEALTH_ARGS = [
    # Core automation hiding
    "--disable-blink-features=AutomationControlled",
    # ⚠️ SECURITY WARNING: The following flags disable critical browser security features.
    # These are required for stealth mode but should ONLY be used when:
    # 1. Processing trusted/untrusted content in an isolated environment
    # 2. The browser instance is not used for browsing arbitrary URLs
    # 3. No sensitive data is accessible from the browser context
    #
    # DO NOT use these flags when:
    # - Loading arbitrary URLs from untrusted sources
    # - The browser has access to credentials or session cookies
    # - Running in a shared/multi-user environment
    #
    # Disabled features and their risks:
    # - site-per-process: Disables site isolation (reduces Spectre/Meltdown protection)
    # - web-security: Disables same-origin policy (allows CORS bypasses)
    # - sandbox: Disables Chrome's process sandbox (allows system access if exploited)
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
    "--disable-web-security",
    # Resource optimization
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    # UI and extensions
    "--disable-infobars",
    "--disable-extensions",
    "--disable-default-apps",
    # Window and display settings
    "--window-size=1920,1080",
    "--start-maximized",
    "--force-color-profile=srgb",
    # GPU and rendering
    "--disable-gpu",
    "--disable-software-rasterizer",
    # Background processes and throttling
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-networking",
    # Network and IPC
    "--disable-ipc-flooding-protection",
    "--disable-hang-monitor",
    # Startup and prompts
    "--no-first-run",
    "--no-default-browser-check",
    "--no-service-autorun",
    # Password and credentials
    "--password-store=basic",
    "--use-mock-keychain",
    # Audio
    "--mute-audio",
    "--autoplay-policy=no-user-gesture-required",
    # Metrics and reporting
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--disable-domain-reliability",
    # Sync and cloud features
    "--disable-sync",
    "--disable-translate",
    # Additional privacy
    "--disable-breakpad",
    "--disable-crash-reporter",
    # Performance
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--force-device-scale-factor=1",
]


# Enhanced user agent pool with more variety
ADVANCED_USER_AGENTS = [
    # Chrome 120-123 (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome 120-123 (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome 120-123 (Linux)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Edge 120-123 (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Edge (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    # Firefox 121-124 (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Firefox (Linux)
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Safari 17 (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# Diverse viewport sizes
ADVANCED_VIEWPORT_SIZES = [
    # Common desktop resolutions
    (1920, 1080),  # Full HD (most common)
    (1366, 768),  # Common laptop
    (1440, 900),  # MacBook Pro 13"
    (1536, 864),  # Surface/Windows scaled
    (1280, 720),  # HD
    (1600, 900),  # HD+
    (2560, 1440),  # 2K
    (1680, 1050),  # Legacy wide
    (1280, 1024),  # 5:4 ratio
    (1920, 1200),  # WUXGA
]


# Timezone options for randomization
TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "America/Denver",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Australia/Sydney",
]


# Realistic HTTP headers
REALISTIC_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

_CHROME_VERSION_RE = re.compile(r"Chrome/(\d+)\.\d+\.\d+\.\d+")
_EDGE_RE = re.compile(r"Edg/(\d+)\.\d+\.\d+\.\d+")


def _build_client_hints(user_agent: str) -> dict[str, str]:
    """Build Sec-Ch-Ua headers that match the given User-Agent string.

    Chrome and Edge send client hints; Firefox and Safari do not.
    """
    chrome_match = _CHROME_VERSION_RE.search(user_agent)
    if not chrome_match:
        # Firefox, Safari, or unknown — don't send client hints
        return {}

    chrome_ver = chrome_match.group(1)

    # Detect platform from UA
    if "Windows" in user_agent:
        platform = '"Windows"'
    elif "Macintosh" in user_agent or "Mac OS X" in user_agent:
        platform = '"macOS"'
    elif "Linux" in user_agent:
        platform = '"Linux"'
    else:
        platform = '"Windows"'

    edge_match = _EDGE_RE.search(user_agent)
    if edge_match:
        edge_ver = edge_match.group(1)
        sec_ch_ua = (
            f'"Not_A Brand";v="8", "Chromium";v="{chrome_ver}", "Microsoft Edge";v="{edge_ver}"'
        )
    else:
        sec_ch_ua = (
            f'"Not_A Brand";v="8", "Chromium";v="{chrome_ver}", "Google Chrome";v="{chrome_ver}"'
        )

    return {
        "Sec-Ch-Ua": sec_ch_ua,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": platform,
    }


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
