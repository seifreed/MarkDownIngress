"""Validation rules for runtime ingest configuration."""

from __future__ import annotations

from typing import Any

import markdown_ingress.config_validation as config_validation
from markdown_ingress.config_domain_policy import _normalize_domain_policies

VALID_OUTPUT_FORMATS = config_validation.VALID_OUTPUT_FORMATS
VALID_POLICY_NAMES = config_validation.VALID_POLICY_NAMES

_ensure_str = config_validation.ensure_str
_ensure_optional_int = config_validation.ensure_optional_int
_validate_output_representations = config_validation.validate_output_representations
_validate_output_profile_name = config_validation.validate_output_profile_name
_validate_string_list = config_validation.validate_string_list
_validate_regex_patterns = config_validation.validate_regex_patterns


def validate_ingest_config(config: Any) -> Any:
    """Validate and normalize a runtime IngestConfig-like object in place."""
    valid_modes = config_validation.VALID_MODES
    if not isinstance(config.mode, str):
        raise ValueError(f"mode must be a string, got {type(config.mode).__name__}")
    if config.mode not in valid_modes:
        raise ValueError(f"Invalid mode '{config.mode}'. Must be one of: {', '.join(valid_modes)}")

    config_validation.coerce_shared_config_fields(config)

    config.cache_ttl = _ensure_optional_int("cache_ttl", config.cache_ttl)
    config.policy_name = _ensure_str("policy_name", config.policy_name)
    config.custom_patterns = _validate_string_list("custom_patterns", config.custom_patterns)
    _validate_regex_patterns(config.custom_patterns)
    config.plugin_dirs = _validate_string_list("plugin_dirs", config.plugin_dirs)
    config.domain_policies = _normalize_domain_policies(config.domain_policies)
    if not isinstance(config.output_format, str):
        raise ValueError(
            f"output_format must be a string, got {type(config.output_format).__name__}"
        )
    if not isinstance(config.chunking_strategy, str):
        raise ValueError(
            f"chunking_strategy must be a string, got " f"{type(config.chunking_strategy).__name__}"
        )

    _validate_ingest_security_constraints(config)
    _validate_ingest_output_constraints(config)
    _validate_ingest_numeric_constraints(config)
    return config


def _validate_ingest_security_constraints(config: Any) -> None:
    if config.fetcher_user_agent and (
        "\r" in config.fetcher_user_agent or "\n" in config.fetcher_user_agent
    ):
        raise ValueError("fetcher_user_agent must not contain CR or LF characters")

    if config.policy_name not in VALID_POLICY_NAMES:
        raise ValueError(
            f"Invalid policy_name '{config.policy_name}'. Must be one of: "
            f"{', '.join(VALID_POLICY_NAMES)}"
        )
    if config.policy_name == "moderate":
        config.policy_name = "normal"


def _validate_ingest_output_constraints(config: Any) -> None:
    valid_chunking = config_validation.VALID_CHUNKING_STRATEGIES
    if config.chunking_strategy not in valid_chunking:
        raise ValueError(
            f"Invalid chunking_strategy '{config.chunking_strategy}'. "
            f"Must be one of: {', '.join(valid_chunking)}"
        )

    if config.output_format not in VALID_OUTPUT_FORMATS:
        raise ValueError(
            f"Invalid output_format '{config.output_format}'. Must be one of: "
            f"{', '.join(VALID_OUTPUT_FORMATS)}"
        )
    _validate_output_profile_name(config.output_profile)
    config.output_formats = _validate_output_representations(config.output_formats)

    if not config.reports_dir or not config.reports_dir.strip():
        raise ValueError("reports_dir cannot be empty")


def _validate_ingest_numeric_constraints(config: Any) -> None:
    _validate_ingest_timeout_constraints(config)
    _validate_ingest_threshold_constraints(config)
    _validate_ingest_cache_constraints(config)
    _validate_ingest_chunk_constraints(config)
    _validate_ingest_batch_constraints(config)


def _validate_positive_float(field_name: str, value: float) -> None:
    if value <= 0.0:
        raise ValueError(f"{field_name} must be > 0.0, got {value}")


def _validate_minimum_int(field_name: str, value: int, minimum: int) -> None:
    if value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}, got {value}")


def _validate_ingest_timeout_constraints(config: Any) -> None:
    _validate_positive_float("timeout", config.timeout)
    _validate_positive_float("circuit_breaker_open_seconds", config.circuit_breaker_open_seconds)


def _validate_ingest_threshold_constraints(config: Any) -> None:
    _validate_minimum_int("auto_render_threshold", config.auto_render_threshold, 1)
    if config.render_cost_budget is not None and config.render_cost_budget < 1:
        raise ValueError(
            "render_cost_budget must be >= 1 when provided, " f"got {config.render_cost_budget}"
        )
    if config.domain_request_interval < 0.0:
        raise ValueError(
            f"domain_request_interval must be >= 0.0, got " f"{config.domain_request_interval}"
        )
    _validate_minimum_int("circuit_breaker_threshold", config.circuit_breaker_threshold, 1)


def _validate_ingest_cache_constraints(config: Any) -> None:
    if config.cache_ttl is not None and config.cache_ttl <= 0:
        raise ValueError(f"cache_ttl must be positive when provided, got {config.cache_ttl}")
    if isinstance(config.cache, bool):
        raise ValueError("cache must be a cache backend object or None, got bool")


def _validate_ingest_chunk_constraints(config: Any) -> None:
    if config.chunk_overlap < 0:
        raise ValueError(f"chunk_overlap must be >= 0, got {config.chunk_overlap}")
    _validate_minimum_int("chunk_size", config.chunk_size, 1)
    if config.chunk_overlap >= config.chunk_size:
        raise ValueError(
            f"chunk_overlap ({config.chunk_overlap}) must be less than "
            f"chunk_size ({config.chunk_size})"
        )


def _validate_ingest_batch_constraints(config: Any) -> None:
    _validate_minimum_int("batch_max_concurrent", config.batch_max_concurrent, 1)
    _validate_positive_float("batch_timeout", config.batch_timeout)
