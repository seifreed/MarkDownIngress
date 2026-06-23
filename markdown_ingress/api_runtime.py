"""Runtime config helpers for the public API surface."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Literal, cast

from markdown_ingress.config_models import DomainPolicy, IngestConfig, _validate_output_profile_name
from markdown_ingress.config_validation import Mode
from markdown_ingress.core.config import (
    Config as FileConfig,
)
from markdown_ingress.core.config import (
    _normalize_domain_policies,
    _validate_regex_patterns,
    _validate_string_list,
)
from markdown_ingress.core.interfaces import ICacheBackend
from markdown_ingress.models import SafeDocument

UNSET = object()

_INGEST_MANY_IN_LOOP_ERROR = (
    "ingest_many() cannot run inside an active event loop; use ingest_many_async() instead"
)


def run_ingest_many_blocking[T](coro_factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run an ingest_many coroutine to completion from synchronous code.

    Raises if called while an event loop is already running, since asyncio.run
    cannot nest.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    raise RuntimeError(_INGEST_MANY_IN_LOOP_ERROR)


_NONE_EXPLICIT_RUNTIME_KEYS = (
    "mode",
    "strict",
    "model",
    "timeout",
    "auto_render_threshold",
    "stealth",
    "disable_http2",
    "extreme_mode",
    "extract_metadata",
    "extract_links",
    "advanced_security",
    "use_llm",
    "policy_name",
    "custom_patterns",
    "plugin_dirs",
    "output_format",
    "output_profile",
    "output_formats",
    "extract_blocks",
    "chunking_strategy",
    "chunk_size",
    "chunk_overlap",
    "detect_language",
    "normalize_multilingual",
    "include_security_explanation",
    "include_observability",
    "save_reports",
    "reports_dir",
    "fetcher_user_agent",
    "domain_request_interval",
    "circuit_breaker_threshold",
    "circuit_breaker_open_seconds",
    "domain_policies",
)

_UNSET_EXPLICIT_RUNTIME_KEYS = (
    "allow_local_urls",
    "screenshot",
    "cache",
    "cache_ttl",
    "render_cost_budget",
)

_DEFAULT_NONE_RUNTIME_VALUES: dict[str, object] = {
    "mode": "auto",
    "strict": True,
    "model": "gpt-4",
    "timeout": 30.0,
    "auto_render_threshold": 50,
    "stealth": False,
    "disable_http2": False,
    "extreme_mode": False,
    "extract_metadata": True,
    "extract_links": True,
    "advanced_security": False,
    "use_llm": False,
    "policy_name": "normal",
    "output_format": "text",
    "output_formats": ["markdown"],
    "extract_blocks": False,
    "chunking_strategy": "none",
    "chunk_size": 1200,
    "chunk_overlap": 120,
    "detect_language": True,
    "normalize_multilingual": True,
    "include_security_explanation": True,
    "include_observability": True,
    "save_reports": False,
    "reports_dir": "reports",
    "fetcher_user_agent": "",
    "domain_request_interval": 0.25,
    "circuit_breaker_threshold": 3,
    "circuit_breaker_open_seconds": 30.0,
}

_VALIDATED_RUNTIME_KEYS = frozenset(
    {"custom_patterns", "plugin_dirs", "output_profile", "domain_policies"}
)


class _IsolatedCacheBackend:
    """Per-clone cache handle that preserves behavior without sharing object identity."""

    def __init__(self, backend: ICacheBackend) -> None:
        self.__wrapped__ = backend

    def get(self, key: str) -> SafeDocument | None:
        return self.__wrapped__.get(key)

    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        return self.__wrapped__.set(key, document, ttl=ttl)

    def delete(self, key: str) -> None:
        return self.__wrapped__.delete(key)

    def clear(self) -> None:
        return self.__wrapped__.clear()

    def exists(self, key: str) -> bool:
        return self.__wrapped__.exists(key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__wrapped__, name)


def normalize_runtime_config(config: IngestConfig | FileConfig | None) -> IngestConfig | None:
    """Accept both runtime and file-based config objects on the public API."""
    if config is None:
        return None
    if isinstance(config, FileConfig):
        return config.to_ingest_config()
    return config


def _validate_batch_max_concurrent(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"max_concurrent must be an int, got {type(value).__name__}")
    if value < 1:
        raise ValueError("max_concurrent must be >= 1")
    return value


def resolve_batch_api_options(
    config: IngestConfig | FileConfig | None,
    *,
    timeout=UNSET,
    max_concurrent=UNSET,
) -> tuple[float | None, int]:
    """Resolve batch-only API options with explicit args taking precedence."""
    resolved_timeout = None if timeout is UNSET else timeout
    resolved_max_concurrent = 5 if max_concurrent is UNSET else max_concurrent

    if isinstance(config, FileConfig):
        if timeout is UNSET:
            resolved_timeout = config.batch_timeout
        if max_concurrent is UNSET:
            resolved_max_concurrent = config.batch_max_concurrent
    elif config is not None:
        if timeout is UNSET:
            resolved_timeout = getattr(config, "batch_timeout", None)
        if max_concurrent is UNSET:
            resolved_max_concurrent = getattr(config, "batch_max_concurrent", 5)

    return resolved_timeout, _validate_batch_max_concurrent(resolved_max_concurrent)


def clone_ingest_config(config: IngestConfig) -> IngestConfig:
    """Copy a runtime config so concurrent callers do not mutate shared state."""
    cloned = config.clone()
    if getattr(cloned, "cache", None) is not None:
        cloned.cache = _IsolatedCacheBackend(cast(ICacheBackend, cloned.cache))
    return cloned


def _collect_explicit_runtime_keys(values: Mapping[str, object]) -> set[str]:
    explicit_keys = {key for key in _NONE_EXPLICIT_RUNTIME_KEYS if values.get(key) is not None}
    explicit_keys.update(
        key for key in _UNSET_EXPLICIT_RUNTIME_KEYS if values.get(key) is not UNSET
    )
    return explicit_keys


def _copy_runtime_default(value: object) -> object:
    if isinstance(value, list):
        return list(value)
    return value


def _validate_runtime_override_values(values: Mapping[str, object]) -> dict[str, object]:
    custom_patterns = values["custom_patterns"]
    plugin_dirs = values["plugin_dirs"]
    domain_policies = values["domain_policies"]

    validated_custom_patterns = (
        [] if custom_patterns is None else _validate_string_list("custom_patterns", custom_patterns)
    )
    if custom_patterns is not None:
        _validate_regex_patterns(validated_custom_patterns)

    return {
        "custom_patterns": validated_custom_patterns,
        "plugin_dirs": (
            [] if plugin_dirs is None else _validate_string_list("plugin_dirs", plugin_dirs)
        ),
        "output_profile": _validate_output_profile_name(cast(str | None, values["output_profile"])),
        "domain_policies": (
            [] if domain_policies is None else _normalize_domain_policies(domain_policies)
        ),
    }


def _runtime_value(
    key: str,
    value: object,
    validated_values: Mapping[str, object],
) -> object:
    if key == "output_profile":
        return validated_values[key] or "default"
    if key in _VALIDATED_RUNTIME_KEYS:
        return validated_values[key]
    return value


def _build_default_runtime_kwargs(
    values: Mapping[str, object],
    validated_values: Mapping[str, object],
) -> dict[str, object]:
    kwargs = {
        key: values[key] if values[key] is not None else _copy_runtime_default(default)
        for key, default in _DEFAULT_NONE_RUNTIME_VALUES.items()
    }
    kwargs.update(
        {key: None if values[key] is UNSET else values[key] for key in _UNSET_EXPLICIT_RUNTIME_KEYS}
    )
    kwargs.update(
        {
            "custom_patterns": validated_values["custom_patterns"],
            "plugin_dirs": validated_values["plugin_dirs"],
            "output_profile": validated_values["output_profile"] or "default",
            "domain_policies": validated_values["domain_policies"],
        }
    )
    return kwargs


def _iter_explicit_runtime_overrides(
    values: Mapping[str, object],
    validated_values: Mapping[str, object],
):
    for key in _NONE_EXPLICIT_RUNTIME_KEYS:
        value = values[key]
        if value is not None:
            yield key, _runtime_value(key, value, validated_values)
    for key in _UNSET_EXPLICIT_RUNTIME_KEYS:
        value = values[key]
        if value is not UNSET:
            yield key, value


def _apply_explicit_runtime_overrides(
    runtime_config: IngestConfig,
    values: Mapping[str, object],
    validated_values: Mapping[str, object],
) -> None:
    for key, value in _iter_explicit_runtime_overrides(values, validated_values):
        setattr(runtime_config, key, value)


# Public override bridge; it accepts legacy ingest kwargs then builds IngestConfig.
def build_runtime_config(  # noqa: PLR0913
    config: IngestConfig | FileConfig | None = None,
    mode: Mode | None = None,
    strict: bool | None = None,
    allow_local_urls=UNSET,
    model: str | None = None,
    timeout: float | None = None,
    auto_render_threshold: int | None = None,
    stealth: bool | None = None,
    disable_http2: bool | None = None,
    extreme_mode: bool | None = None,
    screenshot=UNSET,
    extract_metadata: bool | None = None,
    extract_links: bool | None = None,
    advanced_security: bool | None = None,
    use_llm: bool | None = None,
    cache=UNSET,
    cache_ttl=UNSET,
    policy_name: str | None = None,
    custom_patterns: list[str] | None = None,
    plugin_dirs: list[str] | None = None,
    output_format: Literal["text", "json", "markdown"] | None = None,
    output_profile: str | None = None,
    output_formats: list[str] | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    save_reports: bool | None = None,
    reports_dir: str | None = None,
    fetcher_user_agent: str | None = None,
    domain_request_interval: float | None = None,
    circuit_breaker_threshold: int | None = None,
    circuit_breaker_open_seconds: float | None = None,
    render_cost_budget=UNSET,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
) -> IngestConfig:
    """Build an isolated runtime config from file/runtime config plus overrides."""
    values = dict(locals())
    normalized = normalize_runtime_config(config)
    validated_values = _validate_runtime_override_values(values)

    if normalized is None:
        runtime_config = IngestConfig(**_build_default_runtime_kwargs(values, validated_values))
        explicit_build_keys = _collect_explicit_runtime_keys(values)
        object.__setattr__(runtime_config, "_explicit_keys", frozenset(explicit_build_keys))
        return runtime_config.validate()

    runtime_config = clone_ingest_config(normalized)
    explicit_keys: set[str] = set(runtime_config.explicit_keys())
    explicit_keys.update(_collect_explicit_runtime_keys(values))
    _apply_explicit_runtime_overrides(runtime_config, values, validated_values)

    object.__setattr__(runtime_config, "_explicit_keys", frozenset(explicit_keys))
    return runtime_config.validate()
