"""Domain-specific ingestion policy configuration."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import markdown_ingress.config_validation as config_validation
from markdown_ingress.core.ssrf import normalize_domain_pattern

VALID_POLICY_NAMES = config_validation.VALID_POLICY_NAMES

_ensure_bool = config_validation.ensure_bool
_ensure_optional_bool = config_validation.ensure_optional_bool
_ensure_optional_finite_float = config_validation.ensure_optional_finite_float
_ensure_optional_int = config_validation.ensure_optional_int
_ensure_optional_str = config_validation.ensure_optional_str
_ensure_str = config_validation.ensure_str
_validate_optional_string_list = config_validation.validate_optional_string_list
_validate_output_profile_name = config_validation.validate_output_profile_name


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
        _normalize_policy_scalar_fields(self)
        _validate_policy_mode(self.mode)
        _validate_required_domain(self.domain)
        _validate_output_profile_name(self.output_profile)
        _normalize_policy_dom_fields(self)
        _validate_policy_name(self.policy_name)
        _validate_policy_ranges(self)

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


def _normalize_policy_scalar_fields(policy: DomainPolicy) -> None:
    policy.domain = _ensure_str("DomainPolicy.domain", policy.domain)
    policy.include_subdomains = _ensure_bool(
        "DomainPolicy.include_subdomains", policy.include_subdomains
    )
    policy.timeout = _ensure_optional_finite_float("DomainPolicy.timeout", policy.timeout)
    policy.auto_render_threshold = _ensure_optional_int(
        "DomainPolicy.auto_render_threshold", policy.auto_render_threshold
    )
    policy.strict = _ensure_optional_bool("DomainPolicy.strict", policy.strict)
    policy.policy_name = _ensure_optional_str("DomainPolicy.policy_name", policy.policy_name)
    policy.block_threshold = _ensure_optional_finite_float(
        "DomainPolicy.block_threshold", policy.block_threshold
    )
    policy.warn_threshold = _ensure_optional_finite_float(
        "DomainPolicy.warn_threshold", policy.warn_threshold
    )
    policy.request_interval = _ensure_optional_finite_float(
        "DomainPolicy.request_interval", policy.request_interval
    )
    policy.render_cost_budget = _ensure_optional_int(
        "DomainPolicy.render_cost_budget", policy.render_cost_budget
    )
    policy.extract_metadata = _ensure_optional_bool(
        "DomainPolicy.extract_metadata", policy.extract_metadata
    )
    policy.extract_links = _ensure_optional_bool("DomainPolicy.extract_links", policy.extract_links)
    policy.output_profile = _ensure_optional_str(
        "DomainPolicy.output_profile", policy.output_profile
    )
    policy.notes = _ensure_optional_str("DomainPolicy.notes", policy.notes)


def _validate_policy_mode(mode: object | None) -> None:
    if mode is None:
        return
    if not isinstance(mode, str):
        raise ValueError(f"DomainPolicy.mode must be a string, got {type(mode).__name__}")
    if mode not in ("fast", "render", "auto"):
        raise ValueError(
            f"Invalid DomainPolicy.mode '{mode}'. " "Must be one of: fast, render, auto"
        )


def _validate_required_domain(domain: str) -> None:
    if not domain or not domain.strip():
        raise ValueError("DomainPolicy.domain cannot be empty")


def _normalize_policy_dom_fields(policy: DomainPolicy) -> None:
    policy.allowed_tags = _validate_optional_string_list("allowed_tags", policy.allowed_tags)
    policy.blocked_tags = _validate_optional_string_list("blocked_tags", policy.blocked_tags)
    policy.blocked_selectors = _validate_optional_string_list(
        "blocked_selectors", policy.blocked_selectors
    )
    policy.unwrap_selectors = _validate_optional_string_list(
        "unwrap_selectors", policy.unwrap_selectors
    )


def _validate_policy_name(policy_name: str | None) -> None:
    if policy_name is not None and policy_name not in VALID_POLICY_NAMES:
        raise ValueError(
            f"Invalid policy_name '{policy_name}'. "
            f"Must be one of: {', '.join(VALID_POLICY_NAMES)}"
        )


def _validate_probability_threshold(field_name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"DomainPolicy.{field_name} must be between 0.0 and 1.0, " f"got {value}")


def _validate_policy_ranges(policy: DomainPolicy) -> None:
    _validate_probability_threshold("block_threshold", policy.block_threshold)
    _validate_probability_threshold("warn_threshold", policy.warn_threshold)
    if policy.timeout is not None and policy.timeout <= 0.0:
        raise ValueError(f"DomainPolicy.timeout must be > 0.0, got {policy.timeout}")
    if policy.auto_render_threshold is not None and policy.auto_render_threshold < 1:
        raise ValueError(
            "DomainPolicy.auto_render_threshold must be >= 1, "
            f"got {policy.auto_render_threshold}"
        )
    if policy.request_interval is not None and policy.request_interval < 0.0:
        raise ValueError(
            "DomainPolicy.request_interval must be >= 0.0, " f"got {policy.request_interval}"
        )
    if policy.render_cost_budget is not None and policy.render_cost_budget < 1:
        raise ValueError(
            "DomainPolicy.render_cost_budget must be >= 1 when provided, "
            f"got {policy.render_cost_budget}"
        )


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
