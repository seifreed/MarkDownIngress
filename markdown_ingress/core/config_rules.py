"""Validation rules for legacy file configuration."""

from __future__ import annotations

from typing import Any

import markdown_ingress.config_validation as config_validation
from markdown_ingress.config_models import _normalize_domain_policies

VALID_MODES = ("fast", "render", "auto")
VALID_CACHE_TYPES = ("memory", "sqlite")
VALID_OUTPUT_FORMATS = config_validation.VALID_OUTPUT_FORMATS
VALID_CHUNKING_STRATEGIES = ("none", "heading", "size")
VALID_POLICIES = ("permissive", "normal", "strict", "paranoid", "moderate")

_ensure_bool = config_validation.ensure_bool
_ensure_finite_float = config_validation.ensure_finite_float
_ensure_int = config_validation.ensure_int
_ensure_optional_bool = config_validation.ensure_optional_bool
_ensure_optional_int = config_validation.ensure_optional_int
_ensure_screenshot_value = config_validation.ensure_screenshot_value
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
    _coerce_core_config_fields(config)
    _coerce_fetch_cache_fields(config)
    _coerce_output_security_fields(config)
    _coerce_chunk_report_fields(config)

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


def _coerce_core_config_fields(config: Any) -> None:
    if not isinstance(config.mode, str):
        raise ValueError(f"mode must be a string, got {type(config.mode).__name__}")
    config.timeout = _ensure_finite_float("timeout", config.timeout)
    config.auto_render_threshold = _ensure_int(
        "auto_render_threshold", config.auto_render_threshold
    )
    config.strict = _ensure_bool("strict", config.strict)
    config.allow_local_urls = _ensure_optional_bool("allow_local_urls", config.allow_local_urls)
    config.model = _ensure_str("model", config.model)


def _coerce_fetch_cache_fields(config: Any) -> None:
    config.cache_enabled = _ensure_bool("cache_enabled", config.cache_enabled)
    if not isinstance(config.cache_type, str):
        raise ValueError(f"cache_type must be a string, got {type(config.cache_type).__name__}")
    config.cache_ttl = _ensure_int("cache_ttl", config.cache_ttl)
    config.cache_path = _ensure_str("cache_path", config.cache_path)
    config.batch_max_concurrent = _ensure_int("batch_max_concurrent", config.batch_max_concurrent)
    config.batch_timeout = _ensure_finite_float("batch_timeout", config.batch_timeout)
    config.stealth = _ensure_bool("stealth", config.stealth)
    config.disable_http2 = _ensure_bool("disable_http2", config.disable_http2)
    config.extreme_mode = _ensure_bool("extreme_mode", config.extreme_mode)
    config.screenshot = _ensure_screenshot_value("screenshot", config.screenshot)
    config.fetcher_user_agent = _ensure_str("fetcher_user_agent", config.fetcher_user_agent)
    config.domain_request_interval = _ensure_finite_float(
        "domain_request_interval", config.domain_request_interval
    )
    config.circuit_breaker_threshold = _ensure_int(
        "circuit_breaker_threshold", config.circuit_breaker_threshold
    )
    config.circuit_breaker_open_seconds = _ensure_finite_float(
        "circuit_breaker_open_seconds", config.circuit_breaker_open_seconds
    )


def _coerce_output_security_fields(config: Any) -> None:
    config.policy = _ensure_str("policy", config.policy)
    if not isinstance(config.output_format, str):
        raise ValueError(
            f"output_format must be a string, got {type(config.output_format).__name__}"
        )
    config.output_profile = _ensure_str("output_profile", config.output_profile)
    config.extract_blocks = _ensure_bool("extract_blocks", config.extract_blocks)
    config.extract_metadata = _ensure_bool("extract_metadata", config.extract_metadata)
    config.extract_links = _ensure_bool("extract_links", config.extract_links)
    config.advanced_security = _ensure_bool("advanced_security", config.advanced_security)
    config.use_llm = _ensure_bool("use_llm", config.use_llm)
    config.detect_language = _ensure_bool("detect_language", config.detect_language)
    config.normalize_multilingual = _ensure_bool(
        "normalize_multilingual", config.normalize_multilingual
    )
    config.include_security_explanation = _ensure_bool(
        "include_security_explanation", config.include_security_explanation
    )


def _coerce_chunk_report_fields(config: Any) -> None:
    if not isinstance(config.chunking_strategy, str):
        raise ValueError(
            f"chunking_strategy must be a string, got " f"{type(config.chunking_strategy).__name__}"
        )
    config.chunk_size = _ensure_int("chunk_size", config.chunk_size)
    config.chunk_overlap = _ensure_int("chunk_overlap", config.chunk_overlap)
    config.save_reports = _ensure_bool("save_reports", config.save_reports)
    config.reports_dir = _ensure_str("reports_dir", config.reports_dir)
    config.render_cost_budget = _ensure_optional_int(
        "render_cost_budget", config.render_cost_budget
    )
    config.include_observability = _ensure_bool(
        "include_observability", config.include_observability
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
