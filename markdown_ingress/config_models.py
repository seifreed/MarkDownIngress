"""
Configuration dataclasses for MarkDownIngress.

These dataclasses replace long parameter lists for better maintainability.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from typing import Any, Literal, cast

import markdown_ingress.config_output_profiles as output_profiles
import markdown_ingress.config_validation as config_validation
from markdown_ingress.config_domain_policy import (
    DomainPolicy as DomainPolicy,
)
from markdown_ingress.config_domain_policy import (
    _normalize_domain_policies as _normalize_domain_policies,
)
from markdown_ingress.config_ingest_validation import validate_ingest_config
from markdown_ingress.config_render import RenderConfig as RenderConfig

VALID_WAIT_UNTIL = config_validation.VALID_WAIT_UNTIL
VALID_OUTPUT_FORMATS = config_validation.VALID_OUTPUT_FORMATS
VALID_OUTPUT_REPRESENTATIONS = config_validation.VALID_OUTPUT_REPRESENTATIONS
VALID_POLICY_NAMES = config_validation.VALID_POLICY_NAMES
VALID_OUTPUT_PROFILES = config_validation.VALID_OUTPUT_PROFILES

_ensure_bool = config_validation.ensure_bool
_ensure_optional_bool = config_validation.ensure_optional_bool
_ensure_str = config_validation.ensure_str
_ensure_int = config_validation.ensure_int
_ensure_optional_int = config_validation.ensure_optional_int
_ensure_finite_float = config_validation.ensure_finite_float
_ensure_screenshot_value = config_validation.ensure_screenshot_value
_validate_output_representations = config_validation.validate_output_representations
_validate_output_profile_name = config_validation.validate_output_profile_name
_validate_string_list = config_validation.validate_string_list
_validate_regex_patterns = config_validation.validate_regex_patterns
_collect_init_values = config_validation.collect_init_values


class IngestMode(StrEnum):
    """Valid ingestion modes."""

    FAST = "fast"
    RENDER = "render"
    AUTO = "auto"


@dataclass(init=False)
class IngestConfig:
    """
    Configuration for ingest() and Orchestrator.execute().

    Replaces the 14-parameter signatures with a clean config object.
    Groups related parameters (security, rendering, output).
    """

    # Core parameters
    mode: config_validation.Mode = "auto"
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

    chunking_strategy: config_validation.ChunkingStrategy = "none"
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
        return cast(IngestConfig, validate_ingest_config(self))

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
        return output_profiles.output_profile_fields()

    def explicit_keys(self) -> frozenset[str]:
        """Return config fields explicitly set by the caller."""
        return frozenset(getattr(self, "_explicit_keys", frozenset()))

    @classmethod
    def output_profile_defaults(cls, profile: str) -> dict[str, Any]:
        """Return runtime overrides for a named output profile."""
        return output_profiles.output_profile_defaults(profile)

    @classmethod
    def is_known_profile(cls, profile: str) -> bool:
        """Check if a profile name is recognized."""
        return output_profiles.is_known_output_profile(profile)

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
