"""
Configuration dataclasses for MarkDownIngress.

These dataclasses replace long parameter lists for better maintainability.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import MISSING, dataclass, field, fields, replace
from typing import Any, Literal
from urllib.parse import urlsplit

_logger = logging.getLogger(__name__)

# Valid values for RenderConfig.wait_until
VALID_WAIT_UNTIL = ("networkidle", "load", "domcontentloaded")
VALID_OUTPUT_FORMATS = ("text", "json", "markdown")


def _collect_init_values(cls, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[dict[str, Any], frozenset[str]]:
    """Resolve dataclass-style init arguments while preserving explicit keys."""
    init_fields = [config_field for config_field in fields(cls) if config_field.init]
    if len(args) > len(init_fields):
        raise TypeError(
            f"{cls.__name__}.__init__() takes at most {len(init_fields)} positional arguments "
            f"but {len(args)} were given"
        )

    remaining_kwargs = dict(kwargs)
    values: dict[str, Any] = {}
    explicit: set[str] = set()

    for index, config_field in enumerate(init_fields):
        if index < len(args):
            if config_field.name in remaining_kwargs:
                raise TypeError(f"{cls.__name__}.__init__() got multiple values for argument '{config_field.name}'")
            values[config_field.name] = args[index]
            explicit.add(config_field.name)
            continue

        if config_field.name in remaining_kwargs:
            values[config_field.name] = remaining_kwargs.pop(config_field.name)
            explicit.add(config_field.name)
            continue

        if config_field.default is not MISSING:
            values[config_field.name] = copy.deepcopy(config_field.default)
            continue

        if config_field.default_factory is not MISSING:
            values[config_field.name] = config_field.default_factory()
            continue

        raise TypeError(f"{cls.__name__}.__init__() missing required argument: '{config_field.name}'")

    if remaining_kwargs:
        unexpected = next(iter(remaining_kwargs))
        raise TypeError(f"{cls.__name__}.__init__() got an unexpected keyword argument '{unexpected}'")

    return values, frozenset(explicit)


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

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.wait_until not in VALID_WAIT_UNTIL:
            raise ValueError(
                f"Invalid wait_until '{self.wait_until}'. Must be one of: {', '.join(VALID_WAIT_UNTIL)}"
            )


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
    allowed_tags: list[str] = field(default_factory=list)
    blocked_tags: list[str] = field(default_factory=list)
    blocked_selectors: list[str] = field(default_factory=list)
    unwrap_selectors: list[str] = field(default_factory=list)
    notes: str | None = None

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.domain or not self.domain.strip():
            raise ValueError("DomainPolicy.domain cannot be empty")

    def matches(self, url: str) -> bool:
        """Return whether this policy applies to the URL hostname.

        Uses ``urlsplit().hostname`` which strips port numbers automatically,
        so policies match regardless of the port in the URL.
        """
        # urlsplit().hostname returns lowercase and strips port; may be None
        host = (urlsplit(url).hostname or "").lower()
        normalized = self.domain.lower().lstrip(".")
        if not normalized or not host:
            return False
        if self.include_subdomains:
            return host == normalized or host.endswith(f".{normalized}")
        return host == normalized


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
    """Optional cache backend implementing get/set"""

    cache_ttl: int | None = None
    """Optional TTL override when writing to cache"""

    policy_name: str = "normal"
    """Policy profile name ('permissive', 'normal', 'strict', 'paranoid')"""

    custom_patterns: list[str] = field(default_factory=list)
    """Additional regex patterns injected into security scanning"""

    plugin_dirs: list[str] = field(default_factory=list)
    """Plugin directories to load custom patterns from"""

    output_profile: str = "default"
    """Preset output profile ('default', 'llm_safe', 'rag_chunkable', 'for_search', 'for_archive')"""

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
    """Optional per-request render budget in abstract cost units"""

    output_formats: list[str] = field(default_factory=lambda: ["markdown"])
    """Requested output representations, e.g. ['markdown', 'blocks', 'chunks']"""

    _explicit_keys: frozenset[str] = field(default_factory=frozenset, init=False, repr=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        values, explicit = _collect_init_values(type(self), args, kwargs)
        for key, value in values.items():
            setattr(self, key, value)
        self.__post_init__()
        object.__setattr__(self, "_explicit_keys", explicit)

    def validate(self) -> "IngestConfig":
        """Validate the current config state after construction or mutation."""
        valid_modes = ("fast", "render", "auto")
        if self.mode not in valid_modes:
            raise ValueError(
                f"Invalid mode '{self.mode}'. Must be one of: {', '.join(valid_modes)}"
            )

        valid_policies = ("permissive", "normal", "strict", "paranoid", "moderate")
        if self.policy_name not in valid_policies:
            raise ValueError(
                f"Invalid policy_name '{self.policy_name}'. Must be one of: {', '.join(valid_policies)}"
            )
        # Normalize "moderate" to "normal" for policy engine compatibility
        if self.policy_name == "moderate":
            self.policy_name = "normal"

        valid_chunking = ("none", "heading", "size")
        if self.chunking_strategy not in valid_chunking:
            raise ValueError(
                f"Invalid chunking_strategy '{self.chunking_strategy}'. Must be one of: {', '.join(valid_chunking)}"
            )

        if self.output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"Invalid output_format '{self.output_format}'. Must be one of: {', '.join(VALID_OUTPUT_FORMATS)}"
            )

        if not self.reports_dir or not self.reports_dir.strip():
            raise ValueError("reports_dir cannot be empty")

        if self.chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be >= 0, got {self.chunk_overlap}")

        if self.chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {self.chunk_size}")

        # Validate chunk_overlap < chunk_size (but allow edge case where they're equal for no overlap)
        if self.chunk_overlap > self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) cannot exceed chunk_size ({self.chunk_size})"
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

        return self

    def clone(self) -> "IngestConfig":
        """Return a deep runtime-safe copy."""
        field_names = {config_field.name for config_field in fields(self) if config_field.init}
        base = {name: getattr(self, name) for name in field_names}
        base.update(
            custom_patterns=list(self.custom_patterns),
            plugin_dirs=list(self.plugin_dirs),
            domain_policies=[
                replace(
                    dp,
                    allowed_tags=list(dp.allowed_tags),
                    blocked_tags=list(dp.blocked_tags),
                    blocked_selectors=list(dp.blocked_selectors),
                    unwrap_selectors=list(dp.unwrap_selectors),
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
        return profile in ("default", "llm_safe", "rag_chunkable", "for_search", "for_archive")

    def apply_output_profile(self) -> "IngestConfig":
        """Apply preset defaults to a cloned config, preserving explicit overrides."""
        resolved = self.clone()
        explicit = resolved.explicit_keys()
        object.__setattr__(resolved, "_explicit_keys", explicit)
        defaults = self.output_profile_defaults(resolved.output_profile)

        # Check if profile is unknown (not in the known profiles list)
        if not self.is_known_profile(resolved.output_profile):
            _logger.warning("Unknown output profile '%s'. Using default settings.", resolved.output_profile)

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

    def resolve_for_url(self, url: str) -> tuple["IngestConfig", DomainPolicy | None]:
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
            "allowed_tags",
            "blocked_tags",
            "blocked_selectors",
            "unwrap_selectors",
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
