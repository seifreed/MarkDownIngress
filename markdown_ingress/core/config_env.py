"""Environment variable parsing helpers for configuration loading."""

from __future__ import annotations

from collections.abc import Callable

import markdown_ingress.config_validation as config_validation

EnvConverter = Callable[[str], object]
EnvVarMapping = dict[str, tuple[str, EnvConverter]]

_TRUE_STRINGS = frozenset({"true", "1", "yes", "on", "enabled"})
_FALSE_STRINGS = frozenset({"false", "0", "no", "off", "disabled"})


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
