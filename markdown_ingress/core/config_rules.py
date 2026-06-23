"""Validation rules for legacy file configuration."""

from __future__ import annotations

from typing import Any

import markdown_ingress.config_validation as config_validation
from markdown_ingress.config_models import _normalize_domain_policies

VALID_MODES = config_validation.VALID_MODES
VALID_CACHE_TYPES = ("memory", "sqlite")
VALID_OUTPUT_FORMATS = config_validation.VALID_OUTPUT_FORMATS
VALID_CHUNKING_STRATEGIES = config_validation.VALID_CHUNKING_STRATEGIES
VALID_POLICIES = config_validation.VALID_POLICY_NAMES

_ensure_bool = config_validation.ensure_bool
_ensure_int = config_validation.ensure_int
_ensure_str = config_validation.ensure_str
_validate_output_profile_name = config_validation.validate_output_profile_name
_validate_output_representations = config_validation.validate_output_representations
_validate_regex_patterns = config_validation.validate_regex_patterns
_validate_string_list = config_validation.validate_string_list

_MAX_TIMEOUT_SECONDS = 3_600
_MIN_CHUNK_SIZE = 100
_MAX_CHUNK_SIZE = 50_000
_MIN_CHUNK_OVERLAP = 0
_MAX_CHUNK_OVERLAP = 10_000
_MIN_BATCH_CONCURRENCY = 1
_MIN_CACHE_TTL = 1


def validate_config(config: Any) -> None:
    """Validate and normalize a legacy Config-like object in place."""
    _coerce_config_fields(config)

    _validate_literal_fields(config)
    _validate_numeric_ranges(config)

    config.custom_patterns = _validate_string_list("custom_patterns", config.custom_patterns)
    config.plugin_dirs = _validate_string_list("plugin_dirs", config.plugin_dirs)
    config.output_formats = _validate_output_representations(config.output_formats)
    config.domain_policies = _normalize_domain_policies(config.domain_policies)
    _validate_output_profile_name(config.output_profile)

    if config.render_cost_budget is not None and config.render_cost_budget < 1:
        raise ValueError(
            "render_cost_budget must be >= 1 when provided, " f"got {config.render_cost_budget}"
        )

    _validate_regex_patterns(config.custom_patterns)


def _coerce_config_fields(config: Any) -> None:
    if not isinstance(config.mode, str):
        raise ValueError(f"mode must be a string, got {type(config.mode).__name__}")

    config_validation.coerce_shared_config_fields(config)

    config.cache_enabled = _ensure_bool("cache_enabled", config.cache_enabled)
    if not isinstance(config.cache_type, str):
        raise ValueError(f"cache_type must be a string, got {type(config.cache_type).__name__}")
    config.cache_ttl = _ensure_int("cache_ttl", config.cache_ttl)
    config.cache_path = _ensure_str("cache_path", config.cache_path)
    config.policy = _ensure_str("policy", config.policy)
    if not isinstance(config.output_format, str):
        raise ValueError(
            f"output_format must be a string, got {type(config.output_format).__name__}"
        )
    if not isinstance(config.chunking_strategy, str):
        raise ValueError(
            f"chunking_strategy must be a string, got " f"{type(config.chunking_strategy).__name__}"
        )


def _validate_literal_fields(config: Any) -> None:
    if config.mode not in VALID_MODES:
        raise ValueError(f"Invalid mode '{config.mode}'. Must be one of: {', '.join(VALID_MODES)}")
    if config.cache_type not in VALID_CACHE_TYPES:
        raise ValueError(
            f"Invalid cache_type '{config.cache_type}'. Must be one of: "
            f"{', '.join(VALID_CACHE_TYPES)}"
        )
    if config.output_format not in VALID_OUTPUT_FORMATS:
        raise ValueError(
            f"Invalid output_format '{config.output_format}'. Must be one of: "
            f"{', '.join(VALID_OUTPUT_FORMATS)}"
        )
    if config.chunking_strategy not in VALID_CHUNKING_STRATEGIES:
        raise ValueError(
            f"Invalid chunking_strategy '{config.chunking_strategy}'. Must be one of: "
            f"{', '.join(VALID_CHUNKING_STRATEGIES)}"
        )
    if config.policy not in VALID_POLICIES:
        raise ValueError(
            f"Invalid policy '{config.policy}'. Must be one of: " f"{', '.join(VALID_POLICIES)}"
        )


def _validate_numeric_ranges(config: Any) -> None:
    if config.timeout <= 0 or config.timeout > _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be > 0 and <= {_MAX_TIMEOUT_SECONDS}, got {config.timeout}")
    if config.auto_render_threshold < 1:
        raise ValueError(
            "auto_render_threshold must be >= 1, " f"got {config.auto_render_threshold}"
        )
    if config.batch_max_concurrent < _MIN_BATCH_CONCURRENCY:
        raise ValueError(
            f"batch_max_concurrent must be >= {_MIN_BATCH_CONCURRENCY}, "
            f"got {config.batch_max_concurrent}"
        )
    if config.batch_timeout <= 0:
        raise ValueError(f"batch_timeout must be positive, got {config.batch_timeout}")
    if config.cache_ttl < _MIN_CACHE_TTL:
        raise ValueError(f"cache_ttl must be positive, got {config.cache_ttl}")
    if config.chunk_size < _MIN_CHUNK_SIZE or config.chunk_size > _MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be between {_MIN_CHUNK_SIZE} and {_MAX_CHUNK_SIZE}, "
            f"got {config.chunk_size}"
        )
    if config.chunk_overlap < _MIN_CHUNK_OVERLAP or config.chunk_overlap > _MAX_CHUNK_OVERLAP:
        raise ValueError(
            f"chunk_overlap must be between {_MIN_CHUNK_OVERLAP} and "
            f"{_MAX_CHUNK_OVERLAP}, got {config.chunk_overlap}"
        )
    if config.chunk_overlap >= config.chunk_size:
        raise ValueError(
            f"chunk_overlap ({config.chunk_overlap}) must be less than "
            f"chunk_size ({config.chunk_size})"
        )
