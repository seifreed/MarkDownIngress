"""
Stealth mode module for bypassing bot detection.

This module provides configuration and utilities for making Playwright
browser automation appear more like a regular user browser, helping to
bypass common bot detection mechanisms.
"""

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StealthConfig:
    """Configuration for stealth browsing mode."""
    
    user_agent: str
    viewport_width: int
    viewport_height: int
    locale: str = "en-US"
    timezone: str = "America/New_York"
    browser_args: list[str] = field(default_factory=list)


# Pool of current, realistic browser user agents
USER_AGENTS = [
    # Chrome 120+ (Windows, macOS, Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    
    # Chrome 121+
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    
    # Firefox 121+ (Windows, macOS, Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    
    # Firefox 122+
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
    
    # Safari 17+ (macOS, iOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    
    # Edge 120+ (Windows, macOS)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    
    # Edge 121+
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
]


# Chromium arguments to hide automation and improve stealth
STEALTH_BROWSER_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-infobars',
    '--disable-web-security',
    '--disable-features=IsolateOrigins,site-per-process',
    '--disable-site-isolation-trials',
    '--disable-notifications',
    '--disable-extensions',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-background-timer-throttling',
    '--disable-ipc-flooding-protection',
    '--password-store=basic',
    '--use-mock-keychain',
    '--force-color-profile=srgb',
]


# Common viewport sizes (width, height)
VIEWPORT_SIZES = [
    (1920, 1080),  # Full HD
    (1366, 768),   # Common laptop
    (1440, 900),   # MacBook Pro
    (1536, 864),   # Surface
    (1280, 720),   # HD
    (1600, 900),   # HD+
]


def get_stealth_config() -> StealthConfig:
    """
    Get a randomized stealth configuration.
    
    Returns:
        StealthConfig: Configuration with random user agent, viewport,
                      and stealth browser arguments.
    
    Example:
        >>> config = get_stealth_config()
        >>> print(config.user_agent)
        Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...
        >>> print(config.viewport_width, config.viewport_height)
        1920 1080
    """
    user_agent = random.choice(USER_AGENTS)
    viewport_width, viewport_height = random.choice(VIEWPORT_SIZES)
    
    return StealthConfig(
        user_agent=user_agent,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        locale="en-US",
        timezone="America/New_York",
        browser_args=STEALTH_BROWSER_ARGS.copy(),
    )


def get_context_options(config: StealthConfig | None = None) -> dict[str, Any]:
    """
    Get Playwright browser context options for stealth mode.
    
    Args:
        config: Optional StealthConfig. If not provided, a random one is generated.
    
    Returns:
        dict: Context options suitable for playwright's browser.new_context()
    
    Example:
        >>> options = get_context_options()
        >>> context = await browser.new_context(**options)
    """
    if config is None:
        config = get_stealth_config()
    
    return {
        "user_agent": config.user_agent,
        "viewport": {
            "width": config.viewport_width,
            "height": config.viewport_height,
        },
        "locale": config.locale,
        "timezone_id": config.timezone,
        "bypass_csp": True,
        "ignore_https_errors": True,
        "java_script_enabled": True,
        "has_touch": False,
        "is_mobile": False,
    }
