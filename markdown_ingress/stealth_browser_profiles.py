"""Static browser profile pools for stealth configuration."""

from __future__ import annotations

import re

# ============================================================================
# ULTRA STEALTH BROWSER LAUNCH ARGUMENTS
# ============================================================================

ULTRA_STEALTH_ARGS = [
    # Core automation hiding
    "--disable-blink-features=AutomationControlled",
    # SECURITY WARNING: The following flags disable critical browser security
    # features. Use them only in isolated browser instances.
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


_CHROME_WINDOWS_BASE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " "(KHTML, like Gecko)"
)
_CHROME_MACOS_BASE = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " "(KHTML, like Gecko)"
)
_CHROME_LINUX_BASE = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " "(KHTML, like Gecko)"
_SAFARI_MACOS_BASE = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 " "(KHTML, like Gecko)"
)


def _chrome_user_agent(base: str, version: int) -> str:
    return f"{base} Chrome/{version}.0.0.0 Safari/537.36"


def _edge_user_agent(base: str, version: int) -> str:
    return f"{_chrome_user_agent(base, version)} Edg/{version}.0.0.0"


def _safari_user_agent(version: str) -> str:
    return f"{_SAFARI_MACOS_BASE} Version/{version} Safari/605.1.15"


ADVANCED_USER_AGENTS = [
    # Chrome 120-123 (Windows)
    _chrome_user_agent(_CHROME_WINDOWS_BASE, 120),
    _chrome_user_agent(_CHROME_WINDOWS_BASE, 121),
    _chrome_user_agent(_CHROME_WINDOWS_BASE, 122),
    _chrome_user_agent(_CHROME_WINDOWS_BASE, 123),
    # Chrome 120-123 (macOS)
    _chrome_user_agent(_CHROME_MACOS_BASE, 120),
    _chrome_user_agent(_CHROME_MACOS_BASE, 121),
    _chrome_user_agent(_CHROME_MACOS_BASE, 122),
    _chrome_user_agent(_CHROME_MACOS_BASE, 123),
    # Chrome 120-123 (Linux)
    _chrome_user_agent(_CHROME_LINUX_BASE, 120),
    _chrome_user_agent(_CHROME_LINUX_BASE, 121),
    _chrome_user_agent(_CHROME_LINUX_BASE, 122),
    # Edge 120-123 (Windows)
    _edge_user_agent(_CHROME_WINDOWS_BASE, 120),
    _edge_user_agent(_CHROME_WINDOWS_BASE, 121),
    _edge_user_agent(_CHROME_WINDOWS_BASE, 122),
    # Edge (macOS)
    _edge_user_agent(_CHROME_MACOS_BASE, 120),
    _edge_user_agent(_CHROME_MACOS_BASE, 121),
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
    _safari_user_agent("17.2"),
    _safari_user_agent("17.3"),
    _safari_user_agent("17.4"),
]


ADVANCED_VIEWPORT_SIZES = [
    (1920, 1080),
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1280, 720),
    (1600, 900),
    (2560, 1440),
    (1680, 1050),
    (1280, 1024),
    (1920, 1200),
]


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


REALISTIC_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
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


def build_client_hints(user_agent: str) -> dict[str, str]:
    """Build Sec-Ch-Ua headers that match the given User-Agent string."""
    chrome_match = _CHROME_VERSION_RE.search(user_agent)
    if not chrome_match:
        return {}

    chrome_ver = chrome_match.group(1)

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
