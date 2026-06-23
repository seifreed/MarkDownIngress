"""
Policy engine for configurable security rules
"""

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from markdown_ingress.config_validation import ensure_bool as _ensure_bool
from markdown_ingress.config_validation import ensure_score as _ensure_score
from markdown_ingress.core.security import InjectionPattern, _detect_redos_pattern


def _validate_regex_pattern(pattern: str, description: str) -> None:
    """Validate that a regex pattern doesn't have ReDoS vulnerabilities.

    Args:
        pattern: The regex pattern to validate
        description: Human-readable description for error messages

    Raises:
        ValueError: If the pattern may cause ReDoS
    """
    # First, try to compile the pattern to catch syntax errors
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Custom pattern {description!r} has invalid regex syntax: {e}") from e

    if _detect_redos_pattern(pattern):
        raise ValueError(
            f"Custom pattern {description!r} may cause catastrophic backtracking (ReDoS). "
            f"Pattern contains nested quantifiers or overlapping alternatives that can cause "
            f"exponential time complexity on certain inputs."
        )


class UnsupportedContentTypeError(ValueError):
    """Raised when the fetched response is not HTML-like content."""


class DomainCircuitOpenError(RuntimeError):
    """Raised when a per-domain circuit breaker is open."""


class PolicyBlockedError(RuntimeError):
    """Raised when a policy decides the ingested content must be blocked."""

    def __init__(self, message: str, *, document: Any | None = None):
        super().__init__(message)
        self.document = document


@dataclass
class Policy:
    """Security policy configuration"""

    # Thresholds
    block_threshold: float = 0.7  # Block if injection_score >= this
    warn_threshold: float = 0.4  # Warn if injection_score >= this

    # Pattern weights override
    custom_pattern_weights: dict[str, float] = field(default_factory=dict)

    # Enabled checks
    check_hidden_content: bool = True
    check_injection_patterns: bool = True
    check_imperative_density: bool = True

    # Custom patterns (list of InjectionPattern)
    custom_patterns: list[InjectionPattern] = field(default_factory=list)

    # Strictness level: 'permissive', 'normal', 'strict', 'paranoid'
    strictness: str = "normal"

    # Feature flags
    allow_embedded_scripts: bool = False
    allow_iframes: bool = False
    allow_external_resources: bool = True

    # Metadata
    name: str = "default"
    description: str = "Default security policy"

    def __post_init__(self) -> None:
        """Validate policy configuration."""
        self.block_threshold = _ensure_score("block_threshold", self.block_threshold)
        self.warn_threshold = _ensure_score("warn_threshold", self.warn_threshold)
        self.check_hidden_content = _ensure_bool("check_hidden_content", self.check_hidden_content)
        self.check_injection_patterns = _ensure_bool(
            "check_injection_patterns", self.check_injection_patterns
        )
        self.check_imperative_density = _ensure_bool(
            "check_imperative_density", self.check_imperative_density
        )
        self.allow_embedded_scripts = _ensure_bool(
            "allow_embedded_scripts", self.allow_embedded_scripts
        )
        self.allow_iframes = _ensure_bool("allow_iframes", self.allow_iframes)
        self.allow_external_resources = _ensure_bool(
            "allow_external_resources", self.allow_external_resources
        )
        # warn_threshold >= block_threshold is invalid: both thresholds at same or wrong order
        if self.warn_threshold >= self.block_threshold:
            raise ValueError("warn_threshold must be < block_threshold")
        if self.strictness not in ["permissive", "normal", "strict", "paranoid"]:
            raise ValueError("strictness must be one of: permissive, normal, strict, paranoid")
        # Validate custom_pattern_weights values
        if not isinstance(self.custom_pattern_weights, Mapping):
            raise ValueError(
                "custom_pattern_weights must be a mapping, "
                f"got {type(self.custom_pattern_weights).__name__}"
            )
        normalized_weights: dict[str, float] = {}
        for name, weight in self.custom_pattern_weights.items():
            if not isinstance(name, str):
                raise ValueError(
                    "custom_pattern_weights keys must be strings, " f"got {type(name).__name__}"
                )
            normalized_weights[name] = _ensure_score(f"custom_pattern_weights[{name!r}]", weight)
        self.custom_pattern_weights = normalized_weights
        # Validate custom_patterns weights and check for ReDoS vulnerabilities
        if not isinstance(self.custom_patterns, list):
            raise ValueError(
                f"custom_patterns must be a list, got {type(self.custom_patterns).__name__}"
            )
        for pattern in self.custom_patterns:
            if not isinstance(pattern, InjectionPattern):
                raise ValueError(
                    "custom_patterns entries must be InjectionPattern instances, "
                    f"got {type(pattern).__name__}"
                )
            pattern.weight = _ensure_score(
                f"Custom pattern {pattern.description!r} weight", pattern.weight
            )
            # Validate regex pattern for ReDoS vulnerabilities
            _validate_regex_pattern(pattern.pattern, pattern.description)


# Predefined policy registry — module-level so it's independent of PolicyEngine
POLICIES: dict[str, Policy] = {
    "permissive": Policy(
        name="permissive",
        description="Minimal security checks, maximum compatibility",
        block_threshold=0.9,
        warn_threshold=0.6,
        strictness="permissive",
        check_imperative_density=False,
    ),
    "normal": Policy(
        name="normal",
        description="Balanced security and usability",
        block_threshold=0.7,
        warn_threshold=0.4,
        strictness="normal",
    ),
    "strict": Policy(
        name="strict",
        description="Enhanced security, may have false positives",
        block_threshold=0.5,
        warn_threshold=0.2,
        strictness="strict",
    ),
    "paranoid": Policy(
        name="paranoid",
        description="Maximum security, high false positive rate",
        block_threshold=0.3,
        warn_threshold=0.1,
        strictness="paranoid",
        allow_embedded_scripts=False,
        allow_iframes=False,
    ),
}


def policy_engine_from_name(name: str) -> "PolicyEngine":
    """Create a PolicyEngine from a predefined policy name."""
    if name not in POLICIES:
        raise ValueError(f"Unknown policy: {name}. Use 'list_policies()' to see available options.")
    return PolicyEngine(policy=copy.deepcopy(POLICIES[name]))


def policy_engine_from_dict(config: dict[str, Any]) -> "PolicyEngine":
    """Create a PolicyEngine from a configuration dictionary."""
    config_copy = copy.deepcopy(config)
    custom_patterns: list[InjectionPattern] = []
    if "custom_patterns" in config_copy:
        custom_patterns.extend(
            InjectionPattern(**pattern_dict) for pattern_dict in config_copy["custom_patterns"]
        )
        config_copy["custom_patterns"] = custom_patterns
    return PolicyEngine(policy=Policy(**config_copy))


class PolicyEngine:
    """Evaluate security policies. Construction via from_name() or from_dict()."""

    # Class-level alias for backward compatibility
    POLICIES = POLICIES

    def __init__(self, policy: Policy | None = None):
        self.policy = (
            copy.deepcopy(policy) if policy is not None else copy.deepcopy(POLICIES["normal"])
        )

    @classmethod
    def from_name(cls, name: str) -> "PolicyEngine":
        return policy_engine_from_name(name)

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "PolicyEngine":
        return policy_engine_from_dict(config)

    def _validate_score(self, injection_score: float) -> float:
        """Validate and normalize injection score.

        Args:
            injection_score: Raw injection score

        Returns:
            Validated score clamped to [0.0, 1.0]

        Note:
            NaN and infinity values are treated as maximum risk (1.0) for safety.
        """
        if isinstance(injection_score, bool) or not isinstance(injection_score, (int, float)):
            raise ValueError(
                f"injection_score must be a finite number, got {type(injection_score).__name__}"
            )
        if math.isnan(injection_score) or math.isinf(injection_score):
            # Treat invalid scores as maximum risk for security
            return 1.0
        # Clamp to valid range
        return max(0.0, min(1.0, injection_score))

    def should_block(self, injection_score: float) -> bool:
        """
        Determine if content should be blocked based on injection score.

        Args:
            injection_score: Injection risk score (0.0-1.0)

        Returns:
            True if content should be blocked
        """
        score = self._validate_score(injection_score)
        return score >= self.policy.block_threshold

    def should_warn(self, injection_score: float) -> bool:
        """
        Determine if content should trigger a warning.

        Args:
            injection_score: Injection risk score (0.0-1.0)

        Returns:
            True if content should trigger warning
        """
        score = self._validate_score(injection_score)
        return score >= self.policy.warn_threshold

    def get_action(self, injection_score: float) -> str:
        """
        Get recommended action based on injection score.

        Args:
            injection_score: Injection risk score (0.0-1.0)

        Returns:
            Action string: 'allow', 'warn', or 'block'
        """
        if self.should_block(injection_score):
            return "block"
        elif self.should_warn(injection_score):
            return "warn"
        else:
            return "allow"

    def get_patterns(self) -> list[InjectionPattern]:
        """
        Get all patterns including custom ones.

        Returns:
            List of InjectionPattern objects

        Weight Override Behavior:
            When custom_pattern_weights contains a weight for a pattern description:
            1. If a default pattern has that description, the weight is applied to it
            2. If a custom pattern has that description AND no default pattern has it,
               the weight is applied to the custom pattern
            3. If both a default and custom pattern have the same description, the
               weight override applies ONLY to the default pattern (custom gets its
               original weight from the policy definition)

        Custom Pattern Same Description as Default:
            If a custom pattern has the same description as a default pattern:
            - The default pattern appears first with its weight (possibly overridden)
            - The custom pattern appears after with its original weight from definition
            - Both patterns will be checked during analysis
            - This allows users to add more specific patterns alongside defaults
        """
        from markdown_ingress.core.security import SecurityAnalyzer

        # Get default patterns (deep copy to avoid mutating shared class variable)
        patterns = [copy.copy(p) for p in SecurityAnalyzer.INJECTION_PATTERNS]

        # Track which descriptions we've already applied weight overrides to
        # This prevents applying overrides to both default and custom patterns
        # with the same description
        applied_overrides: set[str] = set()

        # Apply weight overrides to default patterns first
        if self.policy.custom_pattern_weights:
            for pattern in patterns:
                if pattern.description in self.policy.custom_pattern_weights:
                    pattern.weight = self.policy.custom_pattern_weights[pattern.description]
                    applied_overrides.add(pattern.description)

        # Add custom patterns (with their original weights, no override applied)
        for custom_pattern in self.policy.custom_patterns:
            # Create a copy to avoid mutating the original
            pattern_copy = copy.copy(custom_pattern)
            # Apply weight override ONLY if not already applied to a default pattern
            if (
                pattern_copy.description in self.policy.custom_pattern_weights
                and pattern_copy.description not in applied_overrides
            ):
                pattern_copy.weight = self.policy.custom_pattern_weights[pattern_copy.description]
            patterns.append(pattern_copy)

        return patterns

    def to_dict(self) -> dict[str, Any]:
        """
        Export policy as dictionary.

        Returns:
            Policy configuration dictionary
        """
        return {
            "name": self.policy.name,
            "description": self.policy.description,
            "block_threshold": self.policy.block_threshold,
            "warn_threshold": self.policy.warn_threshold,
            "strictness": self.policy.strictness,
            "check_hidden_content": self.policy.check_hidden_content,
            "check_injection_patterns": self.policy.check_injection_patterns,
            "check_imperative_density": self.policy.check_imperative_density,
            "allow_embedded_scripts": self.policy.allow_embedded_scripts,
            "allow_iframes": self.policy.allow_iframes,
            "allow_external_resources": self.policy.allow_external_resources,
            "custom_pattern_weights": dict(self.policy.custom_pattern_weights),
            "custom_patterns": [
                {
                    "pattern": p.pattern,
                    "weight": p.weight,
                    "description": p.description,
                    "flags": p.flags,
                }
                for p in self.policy.custom_patterns
            ],
        }
