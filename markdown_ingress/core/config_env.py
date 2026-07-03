"""Environment variable parsing helpers for configuration loading."""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable

import markdown_ingress.config_validation as config_validation

_logger = logging.getLogger(__name__)
EnvConverter = Callable[[str], object]
EnvVarMapping = dict[str, tuple[str, EnvConverter]]

_TRUE_STRINGS = frozenset({"true", "1", "yes", "on", "enabled"})
_FALSE_STRINGS = frozenset({"false", "0", "no", "off", "disabled"})


def read_env(name: str) -> str | None:
    """Read an environment variable as a raw string."""
    return os.getenv(name)


def read_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean-like environment value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_STRINGS:
        return True
    if normalized in _FALSE_STRINGS:
        return False
    _logger.warning(
        "Invalid boolean for %s=%r. Using default %s.",
        name,
        raw,
        default,
    )
    return default


def read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a non-empty integer >= minimum, otherwise return ``default``."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _logger.warning("Invalid integer for %s=%r. Using default %d.", name, raw, default)
        return default
    if value < minimum:
        _logger.warning(
            "Invalid value for %s=%r. Minimum is %d. Using default %d.",
            name,
            raw,
            minimum,
            default,
        )
        return default
    return value


def read_float_env(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    """Read a finite float within bounds, otherwise return ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("Invalid float for %s=%r. Using default %.2f.", name, raw, default)
        return default
    if not math.isfinite(value):
        _logger.warning("Invalid float for %s=%r. Using default %.2f.", name, raw, default)
        return default
    if value < minimum or (maximum is not None and value > maximum):
        comparator = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        _logger.warning(
            "Invalid float for %s=%r. Expected %s. Using default %.2f.",
            name,
            raw,
            comparator,
            default,
        )
        return default
    return value


def read_optional_float_env(
    name: str, *, minimum: float = 0.0, exclusive_minimum: bool = False
) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("Invalid float for %s=%r. Disabling optional setting.", name, raw)
        return None
    if not math.isfinite(value):
        _logger.warning("Invalid float for %s=%r. Disabling optional setting.", name, raw)
        return None
    is_invalid = value < minimum or (exclusive_minimum and value == minimum)
    if is_invalid:
        comparator = ">" if exclusive_minimum else ">="
        _logger.warning(
            "Invalid value for %s=%r. Expected %s %s. Disabling optional setting.",
            name,
            raw,
            comparator,
            minimum,
        )
        return None
    return value


def parse_csv_string_list(field_name: str, value: str) -> list[str]:
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    return config_validation.validate_string_list(field_name, parsed)


def str_to_bool(value: str) -> bool:
    lower = value.strip().lower()
    if lower in _TRUE_STRINGS:
        return True
    if lower in _FALSE_STRINGS:
        return False
    raise ValueError(
        f"invalid boolean {value!r}; expected one of "
        "true/false/1/0/yes/no/on/off/enabled/disabled"
    )


def str_to_bool_or_string(value: str) -> bool | str:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered in _TRUE_STRINGS:
        return True
    if lowered in _FALSE_STRINGS:
        return False
    return normalized


def build_env_var_mapping(
    *,
    bool_converter: EnvConverter = str_to_bool,
    bool_or_string_converter: EnvConverter = str_to_bool_or_string,
) -> EnvVarMapping:
    return {
        "MDI_MODE": ("mode", str),
        "MDI_TIMEOUT": ("timeout", float),
        "MDI_AUTO_RENDER_THRESHOLD": ("auto_render_threshold", int),
        "MDI_STRICT": ("strict", bool_converter),
        "MDI_ALLOW_LOCAL_URLS": ("allow_local_urls", bool_converter),
        "MDI_MODEL": ("model", str),
        "MDI_CACHE_ENABLED": ("cache_enabled", bool_converter),
        "MDI_CACHE_TYPE": ("cache_type", str),
        "MDI_CACHE_TTL": ("cache_ttl", int),
        "MDI_CACHE_PATH": ("cache_path", str),
        "MDI_BATCH_MAX_CONCURRENT": ("batch_max_concurrent", int),
        "MDI_BATCH_TIMEOUT": ("batch_timeout", float),
        "MDI_POLICY": ("policy", str),
        "MDI_POLICY_NAME": ("policy", str),
        "MDI_OUTPUT_FORMAT": ("output_format", str),
        "MDI_OUTPUT_PROFILE": ("output_profile", str),
        "MDI_EXTRACT_BLOCKS": ("extract_blocks", bool_converter),
        "MDI_EXTRACT_METADATA": ("extract_metadata", bool_converter),
        "MDI_EXTRACT_LINKS": ("extract_links", bool_converter),
        "MDI_ADVANCED_SECURITY": ("advanced_security", bool_converter),
        "MDI_USE_LLM": ("use_llm", bool_converter),
        "MDI_DETECT_LANGUAGE": ("detect_language", bool_converter),
        "MDI_NORMALIZE_MULTILINGUAL": ("normalize_multilingual", bool_converter),
        "MDI_INCLUDE_SECURITY_EXPLANATION": (
            "include_security_explanation",
            bool_converter,
        ),
        "MDI_CHUNKING_STRATEGY": ("chunking_strategy", str),
        "MDI_CHUNK_SIZE": ("chunk_size", int),
        "MDI_CHUNK_OVERLAP": ("chunk_overlap", int),
        "MDI_SAVE_REPORTS": ("save_reports", bool_converter),
        "MDI_REPORTS_DIR": ("reports_dir", str),
        "MDI_RENDER_COST_BUDGET": ("render_cost_budget", int),
        "MDI_INCLUDE_OBSERVABILITY": ("include_observability", bool_converter),
        "MDI_STEALTH": ("stealth", bool_converter),
        "MDI_DISABLE_HTTP2": ("disable_http2", bool_converter),
        "MDI_EXTREME_MODE": ("extreme_mode", bool_converter),
        "MDI_SCREENSHOT": ("screenshot", bool_or_string_converter),
        "MDI_FETCHER_USER_AGENT": ("fetcher_user_agent", str),
        "MDI_DOMAIN_REQUEST_INTERVAL": ("domain_request_interval", float),
        "MDI_CIRCUIT_BREAKER_THRESHOLD": ("circuit_breaker_threshold", int),
        "MDI_CIRCUIT_BREAKER_OPEN_SECONDS": ("circuit_breaker_open_seconds", float),
    }
