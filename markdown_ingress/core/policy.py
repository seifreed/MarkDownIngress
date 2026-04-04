"""
Policy engine for configurable security rules
"""

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

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
        raise ValueError(f"Custom pattern {description!r} has invalid regex syntax: {e}")

    if _detect_redos_pattern(pattern):
        raise ValueError(
            f"Custom pattern {description!r} may cause catastrophic backtracking (ReDoS). "
            f"Pattern contains nested quantifiers or overlapping alternatives that can cause "
            f"exponential time complexity on certain inputs."
        )


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

    def __post_init__(self):
        """Validate policy configuration."""
        if not 0.0 <= self.block_threshold <= 1.0:
            raise ValueError("block_threshold must be between 0.0 and 1.0")
        if not 0.0 <= self.warn_threshold <= 1.0:
            raise ValueError("warn_threshold must be between 0.0 and 1.0")
        # warn_threshold > block_threshold is invalid: would warn at scores higher than block threshold
        # warn_threshold == block_threshold is allowed: both thresholds at same level
        if self.warn_threshold > self.block_threshold:
            raise ValueError("warn_threshold must be <= block_threshold")
        if self.strictness not in ["permissive", "normal", "strict", "paranoid"]:
            raise ValueError("strictness must be one of: permissive, normal, strict, paranoid")
        # Validate custom_pattern_weights values
        for name, weight in self.custom_pattern_weights.items():
            if not 0.0 <= weight <= 1.0:
                raise ValueError(f"custom_pattern_weights[{name!r}] must be between 0.0 and 1.0, got {weight}")
        # Validate custom_patterns weights and check for ReDoS vulnerabilities
        for pattern in self.custom_patterns:
            if not 0.0 <= pattern.weight <= 1.0:
                raise ValueError(f"Custom pattern {pattern.description!r} weight must be between 0.0 and 1.0, got {pattern.weight}")
            # Validate regex pattern for ReDoS vulnerabilities
            _validate_regex_pattern(pattern.pattern, pattern.description)


class PolicyEngine:
    """Manage and apply security policies"""

    # Predefined policies
    POLICIES = {
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

    def __init__(self, policy: Policy | None = None):
        """
        Initialize policy engine.

        Args:
            policy: Policy to use (default: normal)
        """
        self.policy = copy.deepcopy(policy) if policy is not None else copy.deepcopy(self.POLICIES["normal"])

    @classmethod
    def from_name(cls, name: str) -> "PolicyEngine":
        """
        Create policy engine from predefined policy name.

        Args:
            name: Policy name ('permissive', 'normal', 'strict', 'paranoid')

        Returns:
            PolicyEngine instance
        """
        if name not in cls.POLICIES:
            raise ValueError(f"Unknown policy: {name}. Use 'list_policies()' to see available options.")
        return cls(policy=copy.deepcopy(cls.POLICIES[name]))

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "PolicyEngine":
        """
        Create policy engine from configuration dictionary.

        Args:
            config: Policy configuration

        Returns:
            PolicyEngine instance
        """
        # Create a deep copy to avoid mutating the input dictionary
        # (nested dicts like custom_pattern_weights need to be copied too)
        config_copy = copy.deepcopy(config)
        # Convert custom_patterns if present
        custom_patterns = []
        if "custom_patterns" in config_copy:
            for pattern_dict in config_copy["custom_patterns"]:
                custom_patterns.append(InjectionPattern(**pattern_dict))
            config_copy["custom_patterns"] = custom_patterns

        policy = Policy(**config_copy)
        return cls(policy=policy)

    def _validate_score(self, injection_score: float) -> float:
        """Validate and normalize injection score.

        Args:
            injection_score: Raw injection score

        Returns:
            Validated score clamped to [0.0, 1.0]

        Note:
            NaN and infinity values are treated as maximum risk (1.0) for safety.
        """
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
                {"pattern": p.pattern, "weight": p.weight, "description": p.description, "flags": p.flags}
                for p in self.policy.custom_patterns
            ],
        }
