"""
Policy engine for configurable security rules
"""

from dataclasses import dataclass, field
from typing import Any

from markdown_ingress.core.security import InjectionPattern


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
        """Validate policy configuration"""
        if not 0.0 <= self.block_threshold <= 1.0:
            raise ValueError("block_threshold must be between 0.0 and 1.0")
        if not 0.0 <= self.warn_threshold <= 1.0:
            raise ValueError("warn_threshold must be between 0.0 and 1.0")
        if self.warn_threshold > self.block_threshold:
            raise ValueError("warn_threshold must be <= block_threshold")
        if self.strictness not in ["permissive", "normal", "strict", "paranoid"]:
            raise ValueError("strictness must be one of: permissive, normal, strict, paranoid")


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
        self.policy = policy or self.POLICIES["normal"]

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
            raise ValueError(f"Unknown policy: {name}. Available: {list(cls.POLICIES.keys())}")
        return cls(policy=cls.POLICIES[name])

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "PolicyEngine":
        """
        Create policy engine from configuration dictionary.

        Args:
            config: Policy configuration

        Returns:
            PolicyEngine instance
        """
        # Convert custom_patterns if present
        custom_patterns = []
        if "custom_patterns" in config:
            for pattern_dict in config["custom_patterns"]:
                custom_patterns.append(InjectionPattern(**pattern_dict))
            config["custom_patterns"] = custom_patterns

        policy = Policy(**config)
        return cls(policy=policy)

    def should_block(self, injection_score: float) -> bool:
        """
        Determine if content should be blocked based on injection score.

        Args:
            injection_score: Injection risk score (0.0-1.0)

        Returns:
            True if content should be blocked
        """
        return injection_score >= self.policy.block_threshold

    def should_warn(self, injection_score: float) -> bool:
        """
        Determine if content should trigger a warning.

        Args:
            injection_score: Injection risk score (0.0-1.0)

        Returns:
            True if content should trigger warning
        """
        return injection_score >= self.policy.warn_threshold

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

        # Get default patterns
        patterns = list(SecurityAnalyzer.INJECTION_PATTERNS)

        # Add custom patterns
        patterns.extend(self.policy.custom_patterns)

        # Apply weight overrides
        if self.policy.custom_pattern_weights:
            for pattern in patterns:
                if pattern.description in self.policy.custom_pattern_weights:
                    pattern.weight = self.policy.custom_pattern_weights[pattern.description]

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
        }
