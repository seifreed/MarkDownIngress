"""
Configuration dataclasses for MarkDownIngress.

These dataclasses replace long parameter lists for better maintainability.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

import markdown_ingress.config_validation as config_validation
from markdown_ingress.core.ssrf import normalize_domain_pattern

VALID_WAIT_UNTIL = config_validation.VALID_WAIT_UNTIL
VALID_OUTPUT_FORMATS = config_validation.VALID_OUTPUT_FORMATS
VALID_OUTPUT_REPRESENTATIONS = config_validation.VALID_OUTPUT_REPRESENTATIONS
VALID_POLICY_NAMES = config_validation.VALID_POLICY_NAMES
VALID_OUTPUT_PROFILES = config_validation.VALID_OUTPUT_PROFILES

_ensure_bool = config_validation.ensure_bool
_ensure_optional_bool = config_validation.ensure_optional_bool
_ensure_str = config_validation.ensure_str
_ensure_optional_str = config_validation.ensure_optional_str
_ensure_int = config_validation.ensure_int
_ensure_optional_int = config_validation.ensure_optional_int
_ensure_finite_float = config_validation.ensure_finite_float
_ensure_optional_finite_float = config_validation.ensure_optional_finite_float
_ensure_screenshot_value = config_validation.ensure_screenshot_value
_validate_output_representations = config_validation.validate_output_representations
_validate_output_profile_name = config_validation.validate_output_profile_name
_validate_optional_string_list = config_validation.validate_optional_string_list
_validate_string_list = config_validation.validate_string_list
_validate_regex_patterns = config_validation.validate_regex_patterns
_collect_init_values = config_validation.collect_init_values


class IngestMode(StrEnum):
    """Valid ingestion modes."""

    FAST = "fast"
    RENDER = "render"
    AUTO = "auto"


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


@dataclass
class DomainPolicy:
    """Domain-specific runtime overrides for ingestion behavior."""

    domain: str
    include_subdomains: bool = True
    mode: Literal["fast", "render", "auto"] | None = None
    timeout: float | None = None
    auto_render_threshold: int | None = None
    strict: bool | None = None
    policy_name: str | None = None
    block_threshold: float | None = None
    warn_threshold: float | None = None
    request_interval: float | None = None
    render_cost_budget: int | None = None
    extract_metadata: bool | None = None
    extract_links: bool | None = None
    output_profile: str | None = None
    allowed_tags: list[str] | None = None
    blocked_tags: list[str] | None = None
    blocked_selectors: list[str] | None = None
    unwrap_selectors: list[str] | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self.domain = _ensure_str("DomainPolicy.domain", self.domain)
        self.include_subdomains = _ensure_bool(
            "DomainPolicy.include_subdomains", self.include_subdomains
        )
        if self.mode is not None:
            if not isinstance(self.mode, str):
                raise ValueError(
                    f"DomainPolicy.mode must be a string, got {type(self.mode).__name__}"
                )
            if self.mode not in ("fast", "render", "auto"):
                raise ValueError(
                    f"Invalid DomainPolicy.mode '{self.mode}'. "
                    "Must be one of: fast, render, auto"
                )
        self.timeout = _ensure_optional_finite_float("DomainPolicy.timeout", self.timeout)
        self.auto_render_threshold = _ensure_optional_int(
            "DomainPolicy.auto_render_threshold", self.auto_render_threshold
        )
        self.strict = _ensure_optional_bool("DomainPolicy.strict", self.strict)
        self.policy_name = _ensure_optional_str("DomainPolicy.policy_name", self.policy_name)
        self.block_threshold = _ensure_optional_finite_float(
            "DomainPolicy.block_threshold", self.block_threshold
        )
        self.warn_threshold = _ensure_optional_finite_float(
            "DomainPolicy.warn_threshold", self.warn_threshold
        )
        self.request_interval = _ensure_optional_finite_float(
            "DomainPolicy.request_interval", self.request_interval
        )
        self.render_cost_budget = _ensure_optional_int(
            "DomainPolicy.render_cost_budget", self.render_cost_budget
        )
        self.extract_metadata = _ensure_optional_bool(
            "DomainPolicy.extract_metadata", self.extract_metadata
        )
        self.extract_links = _ensure_optional_bool("DomainPolicy.extract_links", self.extract_links)
        self.output_profile = _ensure_optional_str(
            "DomainPolicy.output_profile", self.output_profile
        )
        self.notes = _ensure_optional_str("DomainPolicy.notes", self.notes)
        if not self.domain or not self.domain.strip():
            raise ValueError("DomainPolicy.domain cannot be empty")
        _validate_output_profile_name(self.output_profile)
        self.allowed_tags = _validate_optional_string_list("allowed_tags", self.allowed_tags)
        self.blocked_tags = _validate_optional_string_list("blocked_tags", self.blocked_tags)
        self.blocked_selectors = _validate_optional_string_list(
            "blocked_selectors", self.blocked_selectors
        )
        self.unwrap_selectors = _validate_optional_string_list(
            "unwrap_selectors", self.unwrap_selectors
        )
        if self.policy_name is not None and self.policy_name not in VALID_POLICY_NAMES:
            raise ValueError(
                f"Invalid policy_name '{self.policy_name}'. "
                f"Must be one of: {', '.join(VALID_POLICY_NAMES)}"
            )
        if self.block_threshold is not None and not 0.0 <= self.block_threshold <= 1.0:
            raise ValueError(
                "DomainPolicy.block_threshold must be between 0.0 and 1.0, "
                f"got {self.block_threshold}"
            )
        if self.warn_threshold is not None and not 0.0 <= self.warn_threshold <= 1.0:
            raise ValueError(
                "DomainPolicy.warn_threshold must be between 0.0 and 1.0, "
                f"got {self.warn_threshold}"
            )
        if self.timeout is not None and self.timeout <= 0.0:
            raise ValueError(f"DomainPolicy.timeout must be > 0.0, got {self.timeout}")
        if self.auto_render_threshold is not None and self.auto_render_threshold < 1:
            raise ValueError(
                "DomainPolicy.auto_render_threshold must be >= 1, "
                f"got {self.auto_render_threshold}"
            )
        if self.request_interval is not None and self.request_interval < 0.0:
            raise ValueError(
                "DomainPolicy.request_interval must be >= 0.0, " f"got {self.request_interval}"
            )
        if self.render_cost_budget is not None and self.render_cost_budget < 1:
            raise ValueError(
                "DomainPolicy.render_cost_budget must be >= 1 when provided, "
                f"got {self.render_cost_budget}"
            )

    def matches(self, url: str) -> bool:
        """Return whether this policy applies to the URL hostname.

        Uses ``urlsplit().hostname`` which strips port numbers automatically,
        so policies match regardless of the port in the URL.

        Also strips port from the domain field if present (e.g., "example.com:8080"
        becomes "example.com") to ensure consistent matching.
        """
        # urlsplit().hostname returns lowercase and strips port; may be None
        host = normalize_domain_pattern(urlsplit(url).hostname or "")
        domain_normalized = normalize_domain_pattern(self.domain)
        if not domain_normalized or not host:
            return False
        if self.include_subdomains:
            return host == domain_normalized or host.endswith(f".{domain_normalized}")
        return host == domain_normalized


def _normalize_domain_policies(value: object) -> list[DomainPolicy]:
    """Validate and normalize runtime domain policy overrides."""
    if not isinstance(value, list):
        raise ValueError(
            "domain_policies must be a list of DomainPolicy objects or mappings, "
            f"got {type(value).__name__}"
        )
    normalized: list[DomainPolicy] = []
    for index, item in enumerate(value):
        if isinstance(item, DomainPolicy):
            normalized.append(copy.deepcopy(item))
            continue
        if not isinstance(item, Mapping):
            raise ValueError(
                f"domain_policies[{index}] must be a mapping or DomainPolicy, "
                f"got {type(item).__name__}"
            )
        try:
            normalized.append(DomainPolicy(**dict(item)))
        except Exception as exc:
            raise ValueError(f"domain_policies[{index}] is invalid: {exc}") from exc
    return normalized


@dataclass(init=False)
class IngestConfig:
    """
    Configuration for ingest() and Orchestrator.execute().

    Replaces the 14-parameter signatures with a clean config object.
    Groups related parameters (security, rendering, output).
    """

    # Core parameters
    mode: Literal["fast", "render", "auto"] = "auto"
    """Fetching mode: 'fast' (HTTP only), 'render' (Playwright), 'auto' (detect)"""

    strict: bool = True
    """Enable strict security mode (blocks suspicious content)"""

    model: str = "gpt-4"
    """LLM model name for token estimation"""

    timeout: float = 30.0
    """Request timeout in seconds"""

    auto_render_threshold: int = 50
    """Token threshold for auto mode (if fast returns < this, retry with render)"""

    # Rendering parameters (render mode only)
    stealth: bool = False
    """Enable stealth mode to avoid bot detection (render mode only)"""

    disable_http2: bool = False
    """Disable HTTP/2 protocol, use HTTP/1.1 (render mode only)"""

    extreme_mode: bool = False
    """Enable extreme timeouts (up to 300s) and patient waiting (render mode only)"""

    screenshot: bool | str | None = None
    """Screenshot configuration: path (str), True for temp file, None to disable"""

    # Extraction parameters
    extract_metadata: bool = True
    """Extract enriched metadata"""

    extract_links: bool = True
    """Extract and analyze links"""

    # Security parameters
    advanced_security: bool = False
    """Enable Nova-tracer advanced injection detection (v0.8.0)"""

    use_llm: bool = False
    """Enable LLM-based detection tier (slow but most accurate, v0.8.0)"""

    allow_local_urls: bool | None = None
    """Opt-in override allowing localhost/private URLs for trusted local workflows"""

    # Integration parameters
    cache: object | None = None
    """Optional cache backend or opaque cache identity object"""

    cache_ttl: int | None = None
    """Optional TTL override when writing to cache"""

    policy_name: str = "normal"
    """Policy profile name ('permissive', 'normal', 'strict', 'paranoid')"""

    custom_patterns: list[str] = field(default_factory=list)
    """Additional regex patterns injected into security scanning"""

    plugin_dirs: list[str] = field(default_factory=list)
    """Plugin directories to load custom patterns from"""

    output_profile: str = "default"
    """Preset output profile for public output shaping."""

    output_format: Literal["text", "json", "markdown"] = "text"
    """Preferred public output format for config-driven interfaces such as the CLI"""

    extract_blocks: bool = False
    """Extract structured content blocks in addition to markdown"""

    chunking_strategy: Literal["none", "heading", "size"] = "none"
    """Native chunking strategy used to produce stable chunks"""

    chunk_size: int = 1200
    """Target chunk size in characters when chunking is enabled"""

    chunk_overlap: int = 120
    """Overlap size used by size-based chunking"""

    detect_language: bool = True
    """Populate language metadata from HTML attributes and text detection"""

    normalize_multilingual: bool = True
    """Normalize multilingual content and emit normalization metadata"""

    include_security_explanation: bool = True
    """Attach explainable security analysis to SafeDocument output"""

    include_observability: bool = True
    """Attach per-stage timings and operational traces to output metadata"""

    save_reports: bool = False
    """Automatically persist generated SecurityReport JSON artifacts"""

    reports_dir: str = "reports"
    """Directory where auto-saved security reports are written"""

    domain_policies: list[DomainPolicy] = field(default_factory=list)
    """Ordered list of domain-specific overrides"""

    domain_request_interval: float = 0.25
    """Minimum interval between requests to the same host"""

    circuit_breaker_threshold: int = 3
    """Open a per-domain circuit after this many consecutive failures"""

    circuit_breaker_open_seconds: float = 30.0
    """How long a domain circuit stays open before probing again"""

    render_cost_budget: int | None = None
    """Optional per-request render budget in abstract cost units.
    Auto mode may consume up to 5 units including the fast probe before render."""

    output_formats: list[str] = field(default_factory=lambda: ["markdown"])
    """Requested output representations, e.g. ['markdown', 'blocks', 'chunks']"""

    fetcher_user_agent: str = ""
    """Per-request HTTP user agent selected by auto-mode for cache/inflight dedup consistency"""

    # Batch processing parameters
    batch_timeout: float = 30.0
    """Timeout for batch ingest operations in seconds"""

    batch_max_concurrent: int = 5
    """Maximum concurrent requests in batch mode"""

    _explicit_keys: frozenset[str] = field(default_factory=frozenset, init=False, repr=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        values, explicit = _collect_init_values(type(self), args, kwargs)
        for key, value in values.items():
            setattr(self, key, value)
        # Normalize "moderate" → "normal" before freezing _explicit_keys so
        # that downstream profile application sees the canonical value.
        if self.policy_name == "moderate":
            self.policy_name = "normal"
        self.__post_init__()
        object.__setattr__(self, "_explicit_keys", explicit)

    def validate(self) -> IngestConfig:
        """Validate the current config state after construction or mutation."""
        valid_modes = ("fast", "render", "auto")
        if not isinstance(self.mode, str):
            raise ValueError(f"mode must be a string, got {type(self.mode).__name__}")
        if self.mode not in valid_modes:
            raise ValueError(
                f"Invalid mode '{self.mode}'. Must be one of: {', '.join(valid_modes)}"
            )

        self.strict = _ensure_bool("strict", self.strict)
        self.model = _ensure_str("model", self.model)
        self.timeout = _ensure_finite_float("timeout", self.timeout)
        self.auto_render_threshold = _ensure_int(
            "auto_render_threshold", self.auto_render_threshold
        )
        self.stealth = _ensure_bool("stealth", self.stealth)
        self.disable_http2 = _ensure_bool("disable_http2", self.disable_http2)
        self.extreme_mode = _ensure_bool("extreme_mode", self.extreme_mode)
        self.screenshot = _ensure_screenshot_value("screenshot", self.screenshot)
        self.extract_metadata = _ensure_bool("extract_metadata", self.extract_metadata)
        self.extract_links = _ensure_bool("extract_links", self.extract_links)
        self.advanced_security = _ensure_bool("advanced_security", self.advanced_security)
        self.use_llm = _ensure_bool("use_llm", self.use_llm)
        self.allow_local_urls = _ensure_optional_bool("allow_local_urls", self.allow_local_urls)
        self.cache_ttl = _ensure_optional_int("cache_ttl", self.cache_ttl)
        self.policy_name = _ensure_str("policy_name", self.policy_name)
        self.custom_patterns = _validate_string_list("custom_patterns", self.custom_patterns)
        _validate_regex_patterns(self.custom_patterns)
        self.plugin_dirs = _validate_string_list("plugin_dirs", self.plugin_dirs)
        self.domain_policies = _normalize_domain_policies(self.domain_policies)
        self.output_profile = _ensure_str("output_profile", self.output_profile)
        if not isinstance(self.output_format, str):
            raise ValueError(
                f"output_format must be a string, got {type(self.output_format).__name__}"
            )
        self.extract_blocks = _ensure_bool("extract_blocks", self.extract_blocks)
        if not isinstance(self.chunking_strategy, str):
            raise ValueError(
                f"chunking_strategy must be a string, got {type(self.chunking_strategy).__name__}"
            )
        self.chunk_size = _ensure_int("chunk_size", self.chunk_size)
        self.chunk_overlap = _ensure_int("chunk_overlap", self.chunk_overlap)
        self.detect_language = _ensure_bool("detect_language", self.detect_language)
        self.normalize_multilingual = _ensure_bool(
            "normalize_multilingual", self.normalize_multilingual
        )
        self.include_security_explanation = _ensure_bool(
            "include_security_explanation", self.include_security_explanation
        )
        self.include_observability = _ensure_bool(
            "include_observability", self.include_observability
        )
        self.save_reports = _ensure_bool("save_reports", self.save_reports)
        self.reports_dir = _ensure_str("reports_dir", self.reports_dir)
        self.domain_request_interval = _ensure_finite_float(
            "domain_request_interval", self.domain_request_interval
        )
        self.circuit_breaker_threshold = _ensure_int(
            "circuit_breaker_threshold", self.circuit_breaker_threshold
        )
        self.circuit_breaker_open_seconds = _ensure_finite_float(
            "circuit_breaker_open_seconds", self.circuit_breaker_open_seconds
        )
        self.render_cost_budget = _ensure_optional_int(
            "render_cost_budget", self.render_cost_budget
        )
        self.fetcher_user_agent = _ensure_str("fetcher_user_agent", self.fetcher_user_agent)
        self.batch_timeout = _ensure_finite_float("batch_timeout", self.batch_timeout)
        self.batch_max_concurrent = _ensure_int("batch_max_concurrent", self.batch_max_concurrent)

        if self.fetcher_user_agent and (
            "\r" in self.fetcher_user_agent or "\n" in self.fetcher_user_agent
        ):
            raise ValueError("fetcher_user_agent must not contain CR or LF characters")

        if self.policy_name not in VALID_POLICY_NAMES:
            raise ValueError(
                f"Invalid policy_name '{self.policy_name}'. Must be one of: "
                f"{', '.join(VALID_POLICY_NAMES)}"
            )
        # Normalize "moderate" → "normal" here too, not just in __init__,
        # because resolve_for_url applies overrides via setattr bypassing __init__.
        if self.policy_name == "moderate":
            self.policy_name = "normal"

        valid_chunking = ("none", "heading", "size")
        if self.chunking_strategy not in valid_chunking:
            raise ValueError(
                f"Invalid chunking_strategy '{self.chunking_strategy}'. "
                f"Must be one of: {', '.join(valid_chunking)}"
            )

        if self.output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"Invalid output_format '{self.output_format}'. Must be one of: "
                f"{', '.join(VALID_OUTPUT_FORMATS)}"
            )
        _validate_output_profile_name(self.output_profile)
        self.output_formats = _validate_output_representations(self.output_formats)

        if not self.reports_dir or not self.reports_dir.strip():
            raise ValueError("reports_dir cannot be empty")

        if self.timeout <= 0.0:
            raise ValueError(f"timeout must be > 0.0, got {self.timeout}")

        if self.auto_render_threshold < 1:
            raise ValueError(
                "auto_render_threshold must be >= 1, " f"got {self.auto_render_threshold}"
            )

        if self.render_cost_budget is not None and self.render_cost_budget < 1:
            raise ValueError(
                "render_cost_budget must be >= 1 when provided, " f"got {self.render_cost_budget}"
            )

        if self.cache_ttl is not None and self.cache_ttl <= 0:
            raise ValueError(f"cache_ttl must be positive when provided, got {self.cache_ttl}")

        if isinstance(self.cache, bool):
            raise ValueError("cache must be a cache backend object or None, got bool")

        if self.chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be >= 0, got {self.chunk_overlap}")

        if self.chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {self.chunk_size}")

        # chunk_overlap must be strictly less than chunk_size to ensure forward progress
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be less than "
                f"chunk_size ({self.chunk_size})"
            )

        if self.domain_request_interval < 0.0:
            raise ValueError(
                f"domain_request_interval must be >= 0.0, got {self.domain_request_interval}"
            )

        if self.circuit_breaker_threshold < 1:
            raise ValueError(
                f"circuit_breaker_threshold must be >= 1, got {self.circuit_breaker_threshold}"
            )

        if self.circuit_breaker_open_seconds <= 0.0:
            raise ValueError(
                "circuit_breaker_open_seconds must be > 0.0, "
                f"got {self.circuit_breaker_open_seconds}"
            )

        if self.batch_max_concurrent < 1:
            raise ValueError(f"batch_max_concurrent must be >= 1, got {self.batch_max_concurrent}")

        if self.batch_timeout <= 0.0:
            raise ValueError(f"batch_timeout must be > 0.0, got {self.batch_timeout}")

        return self

    def clone(self) -> IngestConfig:
        """Return a deep runtime-safe copy."""
        field_names = {config_field.name for config_field in fields(self) if config_field.init}
        base = {name: getattr(self, name) for name in field_names}
        base.update(
            custom_patterns=list(self.custom_patterns),
            plugin_dirs=list(self.plugin_dirs),
            domain_policies=[
                replace(
                    dp,
                    allowed_tags=list(dp.allowed_tags) if dp.allowed_tags is not None else None,
                    blocked_tags=list(dp.blocked_tags) if dp.blocked_tags is not None else None,
                    blocked_selectors=(
                        list(dp.blocked_selectors) if dp.blocked_selectors is not None else None
                    ),
                    unwrap_selectors=(
                        list(dp.unwrap_selectors) if dp.unwrap_selectors is not None else None
                    ),
                )
                for dp in self.domain_policies
            ],
            output_format=self.output_format,
            output_formats=list(self.output_formats),
        )
        cloned = IngestConfig(**base)

        # Preserve runtime-only attributes added during policy resolution without
        # re-injecting them into the dataclass constructor.
        for key, value in self.__dict__.items():
            if key == "_explicit_keys":
                object.__setattr__(cloned, key, frozenset(value))
                continue
            if key.startswith("_") or key in field_names:
                continue
            setattr(cloned, key, copy.deepcopy(value))
        return cloned

    @classmethod
    def output_profile_fields(cls) -> frozenset[str]:
        """Return the set of config fields managed by output profiles."""
        field_names: set[str] = set()
        for profile in ("default", "llm_safe", "rag_chunkable", "for_search", "for_archive"):
            field_names.update(cls.output_profile_defaults(profile).keys())
        return frozenset(field_names)

    def explicit_keys(self) -> frozenset[str]:
        """Return config fields explicitly set by the caller."""
        return frozenset(getattr(self, "_explicit_keys", frozenset()))

    @classmethod
    def output_profile_defaults(cls, profile: str) -> dict[str, Any]:
        """Return runtime overrides for a named output profile."""
        profiles: dict[str, dict[str, Any]] = {
            "default": {},
            "llm_safe": {
                "strict": True,
                "extract_metadata": True,
                "extract_links": True,
                "extract_blocks": True,
                "chunking_strategy": "heading",
                "output_formats": ["markdown", "blocks", "security"],
            },
            "rag_chunkable": {
                "extract_metadata": True,
                "extract_links": True,
                "extract_blocks": True,
                "chunking_strategy": "heading",
                "chunk_size": 900,
                "chunk_overlap": 120,
                "output_formats": ["markdown", "blocks", "chunks"],
            },
            "for_search": {
                "mode": "fast",
                "strict": False,
                "extract_metadata": True,
                "extract_links": True,
                "extract_blocks": True,
                "chunking_strategy": "size",
                "chunk_size": 700,
                "chunk_overlap": 80,
                "output_formats": ["markdown", "blocks", "chunks", "metadata"],
            },
            "for_archive": {
                "mode": "render",
                "strict": True,
                "extract_metadata": True,
                "extract_links": True,
                "extract_blocks": True,
                "chunking_strategy": "none",
                "output_formats": ["markdown", "blocks", "metadata", "security"],
            },
        }
        return profiles.get(profile, {})

    @classmethod
    def is_known_profile(cls, profile: str) -> bool:
        """Check if a profile name is recognized."""
        return profile in VALID_OUTPUT_PROFILES

    def apply_output_profile(self) -> IngestConfig:
        """Apply preset defaults to a cloned config, preserving explicit overrides."""
        resolved = self.clone()
        explicit = resolved.explicit_keys()
        object.__setattr__(resolved, "_explicit_keys", explicit)
        defaults = self.output_profile_defaults(resolved.output_profile)

        # Reject unknown profiles to prevent silent misconfiguration
        if not self.is_known_profile(resolved.output_profile):
            raise ValueError(
                f"Unknown output profile '{resolved.output_profile}'. "
                f"Valid profiles: {', '.join(VALID_OUTPUT_PROFILES)}"
            )

        fresh = IngestConfig()
        # Reset any non-explicit profile-managed fields back to dataclass defaults
        # so a second profile application does not inherit values from the first.
        for key in self.output_profile_fields():
            if key in explicit:
                continue
            setattr(resolved, key, copy.deepcopy(getattr(fresh, key)))

        if not defaults:
            return resolved

        for key, value in defaults.items():
            if key in explicit:
                continue
            setattr(resolved, key, copy.deepcopy(value))
        return resolved

    def resolve_for_url(self, url: str) -> tuple[IngestConfig, DomainPolicy | None]:
        """Apply output profile and the first matching domain policy."""
        resolved = self.apply_output_profile()
        matched: DomainPolicy | None = None
        for policy in resolved.domain_policies:
            if policy.matches(url):
                matched = policy
                break
        if matched is None:
            return resolved, None

        if matched.output_profile:
            resolved.output_profile = matched.output_profile
            # A domain-level output_profile is a true override of profile-managed
            # defaults, even when the base config made those fields explicit.
            # Scalar overrides on the DomainPolicy itself are applied afterwards.
            domain_profile_defaults = self.output_profile_defaults(matched.output_profile)
            domain_explicit = {
                key for key in resolved.explicit_keys() if key not in domain_profile_defaults
            }
            domain_explicit.add("output_profile")
            object.__setattr__(resolved, "_explicit_keys", frozenset(domain_explicit))
            resolved = resolved.apply_output_profile()
        for key in (
            "mode",
            "timeout",
            "auto_render_threshold",
            "strict",
            "policy_name",
            "block_threshold",
            "warn_threshold",
            "extract_metadata",
            "extract_links",
            "render_cost_budget",
        ):
            value = getattr(matched, key)
            if value is not None:
                setattr(resolved, key, value)
        if matched.request_interval is not None:
            resolved.domain_request_interval = matched.request_interval
        return resolved.validate(), matched

    def __post_init__(self) -> None:
        """Validate configuration fields after initialization.

        BUG FIX: Added runtime validation for fields that only had type hints.
        """
        self.validate()
