"""Rendering configuration model."""

from __future__ import annotations

from dataclasses import dataclass, field

import markdown_ingress.config_validation as config_validation

VALID_WAIT_UNTIL = config_validation.VALID_WAIT_UNTIL

_ensure_bool = config_validation.ensure_bool
_ensure_finite_float = config_validation.ensure_finite_float
_ensure_optional_bool = config_validation.ensure_optional_bool
_ensure_optional_str = config_validation.ensure_optional_str
_ensure_screenshot_value = config_validation.ensure_screenshot_value
_ensure_str = config_validation.ensure_str


@dataclass
class RenderConfig:
    """
    Configuration for Renderer (Playwright-based rendering).

    Replaces the 15-parameter Renderer.__init__() signature.
    """

    timeout: float = 30.0
    """Navigation timeout in seconds"""

    wait_until: str = "domcontentloaded"
    """When to consider navigation complete ('networkidle', 'load', 'domcontentloaded')"""

    headless: bool = True
    """Run browser in headless mode"""

    user_agent: str | None = None
    """Custom user agent (optional)"""

    stealth: bool = False
    """Enable stealth mode to avoid bot detection"""

    disable_http2: bool = False
    """Disable HTTP/2 protocol (used for fallback)"""

    extreme_mode: bool = False
    """Enable extreme timeouts (up to 300s) and patient waiting"""

    block_resources: bool = True
    """Enable resource blocking for faster loads"""

    block_images: bool = True
    """Block images when resource blocking enabled"""

    block_fonts: bool = True
    """Block fonts when resource blocking enabled"""

    block_media: bool = True
    """Block media (video/audio) when resource blocking enabled"""

    block_ads: bool = True
    """Block advertising domains when resource blocking enabled"""

    block_trackers: bool = True
    """Block analytics/tracking domains when resource blocking enabled"""

    screenshot: bool | str | None = None
    """Screenshot path (str) or True for temp file, None to disable"""

    allow_local_urls: bool | None = None
    """Opt-in override allowing local/private URLs in renderer SSRF checks"""

    dns_pins: dict[str, str] = field(default_factory=dict)
    """Browser DNS pinning map from logical hostname to validated IP address"""

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self.timeout = _ensure_finite_float("timeout", self.timeout)
        if self.timeout <= 0.0:
            raise ValueError(f"timeout must be > 0.0, got {self.timeout}")
        self.wait_until = _ensure_str("wait_until", self.wait_until)
        self.headless = _ensure_bool("headless", self.headless)
        self.user_agent = _ensure_optional_str("user_agent", self.user_agent)
        self.stealth = _ensure_bool("stealth", self.stealth)
        self.disable_http2 = _ensure_bool("disable_http2", self.disable_http2)
        self.extreme_mode = _ensure_bool("extreme_mode", self.extreme_mode)
        self.block_resources = _ensure_bool("block_resources", self.block_resources)
        self.block_images = _ensure_bool("block_images", self.block_images)
        self.block_fonts = _ensure_bool("block_fonts", self.block_fonts)
        self.block_media = _ensure_bool("block_media", self.block_media)
        self.block_ads = _ensure_bool("block_ads", self.block_ads)
        self.block_trackers = _ensure_bool("block_trackers", self.block_trackers)
        self.screenshot = _ensure_screenshot_value("screenshot", self.screenshot)
        self.allow_local_urls = _ensure_optional_bool("allow_local_urls", self.allow_local_urls)
        if self.wait_until not in VALID_WAIT_UNTIL:
            raise ValueError(
                f"Invalid wait_until '{self.wait_until}'. Must be one of: "
                f"{', '.join(VALID_WAIT_UNTIL)}"
            )
        if not isinstance(self.dns_pins, dict):
            raise ValueError(
                f"dns_pins must be a dict[str, str], got {type(self.dns_pins).__name__}"
            )
        normalized_pins: dict[str, str] = {}
        for key, value in self.dns_pins.items():
            if not isinstance(key, str):
                raise ValueError(f"dns_pins keys must be strings, got {type(key).__name__}")
            if not isinstance(value, str):
                raise ValueError(f"dns_pins[{key!r}] must be a string, got {type(value).__name__}")
            normalized_pins[key] = value
        self.dns_pins = normalized_pins
