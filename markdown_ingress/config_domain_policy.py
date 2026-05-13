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
