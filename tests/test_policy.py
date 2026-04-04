"""Tests for policy engine"""

import pytest

from markdown_ingress.core.policy import Policy, PolicyEngine
from markdown_ingress.core.security import InjectionPattern


def test_policy_creation():
    """Test basic policy creation"""
    policy = Policy(name="test", block_threshold=0.8, warn_threshold=0.5)

    assert policy.name == "test"
    assert policy.block_threshold == 0.8
    assert policy.warn_threshold == 0.5


def test_policy_validation():
    """Test policy validation"""
    # Invalid threshold (> 1.0)
    with pytest.raises(ValueError):
        Policy(block_threshold=1.5)

    # Warn > block
    with pytest.raises(ValueError):
        Policy(block_threshold=0.5, warn_threshold=0.8)

    # Invalid strictness
    with pytest.raises(ValueError):
        Policy(strictness="invalid")


def test_policy_engine_from_name():
    """Test creating policy engine from predefined names"""
    engine = PolicyEngine.from_name("normal")
    assert engine.policy.name == "normal"

    engine_strict = PolicyEngine.from_name("strict")
    assert engine_strict.policy.block_threshold < engine.policy.block_threshold

    # Unknown policy
    with pytest.raises(ValueError):
        PolicyEngine.from_name("unknown")


def test_policy_engine_thresholds():
    """Test policy engine threshold logic"""
    engine = PolicyEngine.from_name("normal")

    # Low score - allow
    assert engine.get_action(0.1) == "allow"
    assert not engine.should_warn(0.1)
    assert not engine.should_block(0.1)

    # Medium score - warn
    assert engine.get_action(0.5) == "warn"
    assert engine.should_warn(0.5)
    assert not engine.should_block(0.5)

    # High score - block
    assert engine.get_action(0.8) == "block"
    assert engine.should_warn(0.8)
    assert engine.should_block(0.8)


def test_predefined_policies():
    """Test all predefined policies"""
    permissive = PolicyEngine.from_name("permissive")
    normal = PolicyEngine.from_name("normal")
    strict = PolicyEngine.from_name("strict")
    paranoid = PolicyEngine.from_name("paranoid")

    # Thresholds should be progressively stricter
    assert permissive.policy.block_threshold > normal.policy.block_threshold
    assert normal.policy.block_threshold > strict.policy.block_threshold
    assert strict.policy.block_threshold > paranoid.policy.block_threshold


def test_custom_patterns():
    """Test adding custom injection patterns"""
    custom_pattern = InjectionPattern(
        pattern=r"\bcustom\s+attack\b", weight=0.9, description="Custom attack pattern"
    )

    policy = Policy(custom_patterns=[custom_pattern])
    engine = PolicyEngine(policy)

    patterns = engine.get_patterns()

    # Should include both default and custom patterns
    assert len(patterns) > 10  # Default patterns
    assert any(p.description == "Custom attack pattern" for p in patterns)


def test_policy_allows_safe_repeated_custom_group():
    custom_pattern = InjectionPattern(
        pattern=r"(abc)+", weight=0.4, description="Safe repeated group"
    )

    policy = Policy(custom_patterns=[custom_pattern])

    assert policy.custom_patterns[0].pattern == r"(abc)+"


def test_policy_to_dict():
    """Test exporting policy as dictionary"""
    engine = PolicyEngine.from_name("strict")
    config = engine.to_dict()

    assert config["name"] == "strict"
    assert "block_threshold" in config
    assert "warn_threshold" in config
    assert config["strictness"] == "strict"


def test_policy_from_dict():
    """Test creating policy from dictionary"""
    config = {
        "name": "custom",
        "description": "Custom policy",
        "block_threshold": 0.6,
        "warn_threshold": 0.3,
        "strictness": "normal",
    }

    engine = PolicyEngine.from_dict(config)

    assert engine.policy.name == "custom"
    assert engine.policy.block_threshold == 0.6
    assert engine.policy.warn_threshold == 0.3
