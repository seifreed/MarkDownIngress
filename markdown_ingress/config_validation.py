"""Shared configuration validation helpers."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import MISSING, fields
from typing import Any, Literal, get_args

Mode = Literal["fast", "render", "auto"]
VALID_MODES: tuple[Mode, ...] = get_args(Mode)

ChunkingStrategy = Literal["none", "heading", "size"]
VALID_CHUNKING_STRATEGIES: tuple[ChunkingStrategy, ...] = get_args(ChunkingStrategy)

OutputFormat = Literal["text", "json", "markdown"]
VALID_OUTPUT_FORMATS: tuple[OutputFormat, ...] = get_args(OutputFormat)

VALID_WAIT_UNTIL = ("networkidle", "load", "domcontentloaded")
VALID_OUTPUT_REPRESENTATIONS = ("markdown", "blocks", "chunks", "metadata", "security")
VALID_POLICY_NAMES = ("permissive", "normal", "strict", "paranoid", "moderate")
VALID_OUTPUT_PROFILES = ("default", "llm_safe", "rag_chunkable", "for_search", "for_archive")


def ensure_bool(field_name: str, value: object) -> bool:
    """Return a boolean value or reject untyped config input."""
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool, got {type(value).__name__}")
    return value


def ensure_optional_bool(field_name: str, value: object) -> bool | None:
    """Return an optional boolean value or reject untyped config input."""
    if value is None:
        return None
    return ensure_bool(field_name, value)


def ensure_str(field_name: str, value: object) -> str:
    """Return a string value or reject untyped config input."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    return value


def ensure_optional_str(field_name: str, value: object) -> str | None:
    """Return an optional string value or reject untyped config input."""
    if value is None:
        return None
    return ensure_str(field_name, value)


def ensure_int(field_name: str, value: object) -> int:
    """Return an integer value or reject ambiguous numeric config input."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int, got {type(value).__name__}")
    return value


def validate_positive_int(field_name: str, value: object) -> int:
    """Return an int that is >= 1, rejecting bools and out-of-range values."""
    validated = ensure_int(field_name, value)
    if validated < 1:
        raise ValueError(f"{field_name} must be >= 1")
    return validated


def ensure_optional_int(field_name: str, value: object) -> int | None:
    """Return an optional integer value or reject ambiguous numeric config input."""
    if value is None:
        return None
    return ensure_int(field_name, value)


def ensure_finite_float(field_name: str, value: object) -> float:
    """Return a finite float value or reject unsafe numeric config input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return numeric


def ensure_optional_finite_float(field_name: str, value: object) -> float | None:
    """Return an optional finite float value or reject unsafe numeric config input."""
    if value is None:
        return None
    return ensure_finite_float(field_name, value)


def ensure_score(field_name: str, value: object) -> float:
    """Return a finite float constrained to the inclusive [0.0, 1.0] range."""
    score = ensure_finite_float(field_name, value)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{field_name} must be between 0.0 and 1.0, got {score}")
    return score


def ensure_screenshot_value(field_name: str, value: object) -> bool | str | None:
    """Return a screenshot option or reject unsupported untyped config input."""
    if value is None or isinstance(value, (bool, str)):
        return value
    raise ValueError(f"{field_name} must be a bool, string, or None, got {type(value).__name__}")


def coerce_shared_config_fields(config: Any) -> None:
    """Coerce in place the fields common to runtime IngestConfig and legacy Config.

    Type-specific fields (policy/policy_name, cache backend settings) and numeric
    range checks stay with each config's own validator.
    """
    config.strict = ensure_bool("strict", config.strict)
    config.model = ensure_str("model", config.model)
    config.timeout = ensure_finite_float("timeout", config.timeout)
    config.auto_render_threshold = ensure_int("auto_render_threshold", config.auto_render_threshold)
    config.stealth = ensure_bool("stealth", config.stealth)
    config.disable_http2 = ensure_bool("disable_http2", config.disable_http2)
    config.extreme_mode = ensure_bool("extreme_mode", config.extreme_mode)
    config.screenshot = ensure_screenshot_value("screenshot", config.screenshot)
    config.extract_metadata = ensure_bool("extract_metadata", config.extract_metadata)
    config.extract_links = ensure_bool("extract_links", config.extract_links)
    config.advanced_security = ensure_bool("advanced_security", config.advanced_security)
    config.use_llm = ensure_bool("use_llm", config.use_llm)
    config.allow_local_urls = ensure_optional_bool("allow_local_urls", config.allow_local_urls)
    config.output_profile = ensure_str("output_profile", config.output_profile)
    config.extract_blocks = ensure_bool("extract_blocks", config.extract_blocks)
    config.chunk_size = ensure_int("chunk_size", config.chunk_size)
    config.chunk_overlap = ensure_int("chunk_overlap", config.chunk_overlap)
    config.detect_language = ensure_bool("detect_language", config.detect_language)
    config.normalize_multilingual = ensure_bool(
        "normalize_multilingual", config.normalize_multilingual
    )
    config.include_security_explanation = ensure_bool(
        "include_security_explanation", config.include_security_explanation
    )
    config.include_observability = ensure_bool(
        "include_observability", config.include_observability
    )
    config.save_reports = ensure_bool("save_reports", config.save_reports)
    config.reports_dir = ensure_str("reports_dir", config.reports_dir)
    config.fetcher_user_agent = ensure_str("fetcher_user_agent", config.fetcher_user_agent)
    config.domain_request_interval = ensure_finite_float(
        "domain_request_interval", config.domain_request_interval
    )
    config.circuit_breaker_threshold = ensure_int(
        "circuit_breaker_threshold", config.circuit_breaker_threshold
    )
    config.circuit_breaker_open_seconds = ensure_finite_float(
        "circuit_breaker_open_seconds", config.circuit_breaker_open_seconds
    )
    config.render_cost_budget = ensure_optional_int("render_cost_budget", config.render_cost_budget)
    config.batch_max_concurrent = ensure_int("batch_max_concurrent", config.batch_max_concurrent)
    config.batch_timeout = ensure_finite_float("batch_timeout", config.batch_timeout)


def validate_output_representations(value: list[str]) -> list[str]:
    """Validate requested document output representations."""
    if not isinstance(value, list):
        raise ValueError(
            "output_formats must be a non-empty list of supported format strings, "
            f"got {type(value).__name__}"
        )
    if not value:
        raise ValueError("output_formats must be a non-empty list of supported format strings")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"output_formats[{index}] must be a string, got {type(item).__name__}")
        if item not in VALID_OUTPUT_REPRESENTATIONS:
            raise ValueError(
                "Invalid output_formats entry "
                f"'{item}'. Must be one of: {', '.join(VALID_OUTPUT_REPRESENTATIONS)}"
            )
        normalized.append(item)
    return normalized


def validate_output_profile_name(value: str | None) -> str | None:
    """Validate a named output profile."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"output_profile must be a string or None, got {type(value).__name__}")
    if value not in VALID_OUTPUT_PROFILES:
        raise ValueError(
            f"Unknown output profile '{value}'. "
            f"Valid profiles: {', '.join(VALID_OUTPUT_PROFILES)}"
        )
    return value


def validate_optional_string_list(field_name: str, value: list[str] | None) -> list[str] | None:
    """Validate optional list[str] fields used by domain-specific rules."""
    if value is None:
        return None
    return validate_string_list(field_name, value)


def validate_string_list(field_name: str, value: object) -> list[str]:
    """Validate required list[str] fields used by runtime configuration."""
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings, got {type(value).__name__}")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string, got {type(item).__name__}")
        normalized.append(item)
    return normalized


def validate_regex_patterns(patterns: list[str]) -> None:
    """Validate user-provided regex patterns before runtime scanning.

    Rejects both invalid syntax and patterns prone to catastrophic backtracking
    (ReDoS), so a malicious pattern is refused at the request/config boundary
    instead of after a URL has already been fetched.
    """
    # Lazy import: config_validation is a low-level module imported very early,
    # so the ReDoS detector is pulled in only when patterns are actually validated.
    from markdown_ingress.core.security_text import _detect_redos_pattern

    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern '{pattern}': {exc}") from exc
        if _detect_redos_pattern(pattern):
            raise ValueError(
                f"Regex pattern may cause catastrophic backtracking (ReDoS): '{pattern}'"
            )


def collect_init_values(
    cls: type[Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], frozenset[str]]:
    """Resolve dataclass-style init arguments while preserving explicit keys."""
    init_fields = [config_field for config_field in fields(cls) if config_field.init]
    if len(args) > len(init_fields):
        raise TypeError(
            f"{cls.__name__}.__init__() takes at most {len(init_fields)} positional arguments "
            f"but {len(args)} were given"
        )

    remaining_kwargs = dict(kwargs)
    values: dict[str, Any] = {}
    explicit: set[str] = set()

    for index, config_field in enumerate(init_fields):
        if index < len(args):
            if config_field.name in remaining_kwargs:
                raise TypeError(
                    f"{cls.__name__}.__init__() got multiple values for argument "
                    f"{config_field.name!r}"
                )
            values[config_field.name] = args[index]
            explicit.add(config_field.name)
            continue

        if config_field.name in remaining_kwargs:
            values[config_field.name] = remaining_kwargs.pop(config_field.name)
            explicit.add(config_field.name)
            continue

        if config_field.default is not MISSING:
            values[config_field.name] = copy.deepcopy(config_field.default)
            continue

        if config_field.default_factory is not MISSING:
            values[config_field.name] = config_field.default_factory()
            continue

        raise TypeError(
            f"{cls.__name__}.__init__() missing required argument: '{config_field.name}'"
        )

    if remaining_kwargs:
        unexpected = next(iter(remaining_kwargs))
        raise TypeError(
            f"{cls.__name__}.__init__() got an unexpected keyword argument '{unexpected}'"
        )

    return values, frozenset(explicit)
