"""Validation rules for runtime ingest configuration."""

from __future__ import annotations

from typing import Any

import markdown_ingress.config_validation as config_validation
from markdown_ingress.config_domain_policy import _normalize_domain_policies

VALID_OUTPUT_FORMATS = config_validation.VALID_OUTPUT_FORMATS
VALID_POLICY_NAMES = config_validation.VALID_POLICY_NAMES

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


def validate_ingest_config(config: Any) -> Any:
    """Validate and normalize a runtime IngestConfig-like object in place."""
    valid_modes = ("fast", "render", "auto")
    if not isinstance(config.mode, str):
        raise ValueError(f"mode must be a string, got {type(config.mode).__name__}")
    if config.mode not in valid_modes:
        raise ValueError(f"Invalid mode '{config.mode}'. Must be one of: {', '.join(valid_modes)}")

    config.strict = _ensure_bool("strict", config.strict)
    config.model = _ensure_str("model", config.model)
    config.timeout = _ensure_finite_float("timeout", config.timeout)
    config.auto_render_threshold = _ensure_int(
        "auto_render_threshold", config.auto_render_threshold
    )
    config.stealth = _ensure_bool("stealth", config.stealth)
    config.disable_http2 = _ensure_bool("disable_http2", config.disable_http2)
    config.extreme_mode = _ensure_bool("extreme_mode", config.extreme_mode)
    config.screenshot = _ensure_screenshot_value("screenshot", config.screenshot)
    config.extract_metadata = _ensure_bool("extract_metadata", config.extract_metadata)
    config.extract_links = _ensure_bool("extract_links", config.extract_links)
    config.advanced_security = _ensure_bool("advanced_security", config.advanced_security)
    config.use_llm = _ensure_bool("use_llm", config.use_llm)
    config.allow_local_urls = _ensure_optional_bool("allow_local_urls", config.allow_local_urls)
    config.cache_ttl = _ensure_optional_int("cache_ttl", config.cache_ttl)
    config.policy_name = _ensure_str("policy_name", config.policy_name)
    config.custom_patterns = _validate_string_list("custom_patterns", config.custom_patterns)
    _validate_regex_patterns(config.custom_patterns)
    config.plugin_dirs = _validate_string_list("plugin_dirs", config.plugin_dirs)
    config.domain_policies = _normalize_domain_policies(config.domain_policies)
    config.output_profile = _ensure_str("output_profile", config.output_profile)
    if not isinstance(config.output_format, str):
        raise ValueError(
            f"output_format must be a string, got {type(config.output_format).__name__}"
        )
    config.extract_blocks = _ensure_bool("extract_blocks", config.extract_blocks)
    if not isinstance(config.chunking_strategy, str):
        raise ValueError(
            f"chunking_strategy must be a string, got " f"{type(config.chunking_strategy).__name__}"
        )
    config.chunk_size = _ensure_int("chunk_size", config.chunk_size)
    config.chunk_overlap = _ensure_int("chunk_overlap", config.chunk_overlap)
    config.detect_language = _ensure_bool("detect_language", config.detect_language)
    config.normalize_multilingual = _ensure_bool(
        "normalize_multilingual", config.normalize_multilingual
    )
    config.include_security_explanation = _ensure_bool(
        "include_security_explanation", config.include_security_explanation
    )
    config.include_observability = _ensure_bool(
        "include_observability", config.include_observability
    )
    config.save_reports = _ensure_bool("save_reports", config.save_reports)
    config.reports_dir = _ensure_str("reports_dir", config.reports_dir)
    config.domain_request_interval = _ensure_finite_float(
        "domain_request_interval", config.domain_request_interval
    )
    config.circuit_breaker_threshold = _ensure_int(
        "circuit_breaker_threshold", config.circuit_breaker_threshold
    )
    config.circuit_breaker_open_seconds = _ensure_finite_float(
        "circuit_breaker_open_seconds", config.circuit_breaker_open_seconds
    )
    config.render_cost_budget = _ensure_optional_int(
        "render_cost_budget", config.render_cost_budget
    )
    config.fetcher_user_agent = _ensure_str("fetcher_user_agent", config.fetcher_user_agent)
    config.batch_timeout = _ensure_finite_float("batch_timeout", config.batch_timeout)
    config.batch_max_concurrent = _ensure_int("batch_max_concurrent", config.batch_max_concurrent)

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
    valid_chunking = ("none", "heading", "size")
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
    if config.timeout <= 0.0:
        raise ValueError(f"timeout must be > 0.0, got {config.timeout}")

    if config.auto_render_threshold < 1:
        raise ValueError(
            "auto_render_threshold must be >= 1, " f"got {config.auto_render_threshold}"
        )

    if config.render_cost_budget is not None and config.render_cost_budget < 1:
        raise ValueError(
            "render_cost_budget must be >= 1 when provided, " f"got {config.render_cost_budget}"
        )

    if config.cache_ttl is not None and config.cache_ttl <= 0:
        raise ValueError(f"cache_ttl must be positive when provided, got {config.cache_ttl}")

    if isinstance(config.cache, bool):
        raise ValueError("cache must be a cache backend object or None, got bool")

    if config.chunk_overlap < 0:
        raise ValueError(f"chunk_overlap must be >= 0, got {config.chunk_overlap}")

    if config.chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {config.chunk_size}")

    if config.chunk_overlap >= config.chunk_size:
        raise ValueError(
            f"chunk_overlap ({config.chunk_overlap}) must be less than "
            f"chunk_size ({config.chunk_size})"
        )

    if config.domain_request_interval < 0.0:
        raise ValueError(
            f"domain_request_interval must be >= 0.0, got " f"{config.domain_request_interval}"
        )

    if config.circuit_breaker_threshold < 1:
        raise ValueError(
            f"circuit_breaker_threshold must be >= 1, " f"got {config.circuit_breaker_threshold}"
        )

    if config.circuit_breaker_open_seconds <= 0.0:
        raise ValueError(
            "circuit_breaker_open_seconds must be > 0.0, "
            f"got {config.circuit_breaker_open_seconds}"
        )

    if config.batch_max_concurrent < 1:
        raise ValueError(f"batch_max_concurrent must be >= 1, got {config.batch_max_concurrent}")

    if config.batch_timeout <= 0.0:
        raise ValueError(f"batch_timeout must be > 0.0, got {config.batch_timeout}")
