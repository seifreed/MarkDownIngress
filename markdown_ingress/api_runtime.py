"""Runtime config helpers for the public API surface."""

from __future__ import annotations

from typing import Literal

from markdown_ingress.config_models import DomainPolicy, IngestConfig, _validate_output_profile_name
from markdown_ingress.core.config import (
    Config as FileConfig,
)
from markdown_ingress.core.config import (
    _normalize_domain_policies,
    _validate_regex_patterns,
    _validate_string_list,
)

UNSET = object()


class _IsolatedCacheBackend:
    """Per-clone cache handle that preserves behavior without sharing object identity."""

    def __init__(self, backend: object) -> None:
        self.__wrapped__ = backend

    def get(self, key):
        return self.__wrapped__.get(key)

    def set(self, key, document, ttl=None):
        return self.__wrapped__.set(key, document, ttl=ttl)

    def delete(self, key):
        return self.__wrapped__.delete(key)

    def clear(self):
        return self.__wrapped__.clear()

    def exists(self, key):
        return self.__wrapped__.exists(key)

    def __getattr__(self, name: str):
        return getattr(self.__wrapped__, name)


def normalize_runtime_config(config: IngestConfig | FileConfig | None) -> IngestConfig | None:
    """Accept both runtime and file-based config objects on the public API."""
    if config is None:
        return None
    if isinstance(config, FileConfig):
        return config.to_ingest_config()
    return config


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

    return resolved_timeout, resolved_max_concurrent


def clone_ingest_config(config: IngestConfig) -> IngestConfig:
    """Copy a runtime config so concurrent callers do not mutate shared state."""
    cloned = config.clone()
    if getattr(cloned, "cache", None) is not None:
        cloned.cache = _IsolatedCacheBackend(cloned.cache)
    return cloned


def build_runtime_config(
    config: IngestConfig | FileConfig | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
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
    normalized = normalize_runtime_config(config)
    validated_custom_patterns = (
        [] if custom_patterns is None else _validate_string_list("custom_patterns", custom_patterns)
    )
    if custom_patterns is not None:
        _validate_regex_patterns(validated_custom_patterns)
    validated_plugin_dirs = (
        [] if plugin_dirs is None else _validate_string_list("plugin_dirs", plugin_dirs)
    )
    validated_domain_policies = (
        [] if domain_policies is None else _normalize_domain_policies(domain_policies)
    )
    validated_output_profile = _validate_output_profile_name(output_profile)

    if normalized is None:
        runtime_config = IngestConfig(
            mode=mode if mode is not None else "auto",
            strict=strict if strict is not None else True,
            allow_local_urls=None if allow_local_urls is UNSET else allow_local_urls,
            model=model if model is not None else "gpt-4",
            timeout=timeout if timeout is not None else 30.0,
            auto_render_threshold=(
                auto_render_threshold if auto_render_threshold is not None else 50
            ),
            stealth=stealth if stealth is not None else False,
            disable_http2=disable_http2 if disable_http2 is not None else False,
            extreme_mode=extreme_mode if extreme_mode is not None else False,
            screenshot=None if screenshot is UNSET else screenshot,
            extract_metadata=extract_metadata if extract_metadata is not None else True,
            extract_links=extract_links if extract_links is not None else True,
            advanced_security=advanced_security if advanced_security is not None else False,
            use_llm=use_llm if use_llm is not None else False,
            cache=None if cache is UNSET else cache,
            cache_ttl=None if cache_ttl is UNSET else cache_ttl,
            policy_name=policy_name if policy_name is not None else "normal",
            custom_patterns=validated_custom_patterns,
            plugin_dirs=validated_plugin_dirs,
            output_format=output_format if output_format is not None else "text",
            output_profile=(
                validated_output_profile if validated_output_profile is not None else "default"
            ),
            output_formats=["markdown"] if output_formats is None else output_formats,
            extract_blocks=extract_blocks if extract_blocks is not None else False,
            chunking_strategy=chunking_strategy if chunking_strategy is not None else "none",
            chunk_size=chunk_size if chunk_size is not None else 1200,
            chunk_overlap=chunk_overlap if chunk_overlap is not None else 120,
            detect_language=detect_language if detect_language is not None else True,
            normalize_multilingual=(
                normalize_multilingual if normalize_multilingual is not None else True
            ),
            include_security_explanation=(
                include_security_explanation if include_security_explanation is not None else True
            ),
            include_observability=(
                include_observability if include_observability is not None else True
            ),
            save_reports=save_reports if save_reports is not None else False,
            reports_dir=reports_dir if reports_dir is not None else "reports",
            fetcher_user_agent=fetcher_user_agent if fetcher_user_agent is not None else "",
            domain_request_interval=(
                domain_request_interval if domain_request_interval is not None else 0.25
            ),
            circuit_breaker_threshold=(
                circuit_breaker_threshold if circuit_breaker_threshold is not None else 3
            ),
            circuit_breaker_open_seconds=(
                circuit_breaker_open_seconds if circuit_breaker_open_seconds is not None else 30.0
            ),
            render_cost_budget=None if render_cost_budget is UNSET else render_cost_budget,
            domain_policies=validated_domain_policies,
        )
        explicit_build_keys: set[str] = set()
        if mode is not None:
            explicit_build_keys.add("mode")
        if strict is not None:
            explicit_build_keys.add("strict")
        if allow_local_urls is not UNSET:
            explicit_build_keys.add("allow_local_urls")
        if model is not None:
            explicit_build_keys.add("model")
        if timeout is not None:
            explicit_build_keys.add("timeout")
        if auto_render_threshold is not None:
            explicit_build_keys.add("auto_render_threshold")
        if stealth is not None:
            explicit_build_keys.add("stealth")
        if disable_http2 is not None:
            explicit_build_keys.add("disable_http2")
        if extreme_mode is not None:
            explicit_build_keys.add("extreme_mode")
        if screenshot is not UNSET:
            explicit_build_keys.add("screenshot")
        if extract_metadata is not None:
            explicit_build_keys.add("extract_metadata")
        if extract_links is not None:
            explicit_build_keys.add("extract_links")
        if advanced_security is not None:
            explicit_build_keys.add("advanced_security")
        if use_llm is not None:
            explicit_build_keys.add("use_llm")
        if cache is not UNSET:
            explicit_build_keys.add("cache")
        if cache_ttl is not UNSET:
            explicit_build_keys.add("cache_ttl")
        if policy_name is not None:
            explicit_build_keys.add("policy_name")
        if custom_patterns is not None:
            explicit_build_keys.add("custom_patterns")
        if plugin_dirs is not None:
            explicit_build_keys.add("plugin_dirs")
        if output_format is not None:
            explicit_build_keys.add("output_format")
        if output_profile is not None:
            explicit_build_keys.add("output_profile")
        if output_formats is not None:
            explicit_build_keys.add("output_formats")
        if extract_blocks is not None:
            explicit_build_keys.add("extract_blocks")
        if chunking_strategy is not None:
            explicit_build_keys.add("chunking_strategy")
        if chunk_size is not None:
            explicit_build_keys.add("chunk_size")
        if chunk_overlap is not None:
            explicit_build_keys.add("chunk_overlap")
        if detect_language is not None:
            explicit_build_keys.add("detect_language")
        if normalize_multilingual is not None:
            explicit_build_keys.add("normalize_multilingual")
        if include_security_explanation is not None:
            explicit_build_keys.add("include_security_explanation")
        if include_observability is not None:
            explicit_build_keys.add("include_observability")
        if save_reports is not None:
            explicit_build_keys.add("save_reports")
        if reports_dir is not None:
            explicit_build_keys.add("reports_dir")
        if fetcher_user_agent is not None:
            explicit_build_keys.add("fetcher_user_agent")
        if domain_request_interval is not None:
            explicit_build_keys.add("domain_request_interval")
        if circuit_breaker_threshold is not None:
            explicit_build_keys.add("circuit_breaker_threshold")
        if circuit_breaker_open_seconds is not None:
            explicit_build_keys.add("circuit_breaker_open_seconds")
        if render_cost_budget is not UNSET:
            explicit_build_keys.add("render_cost_budget")
        if domain_policies is not None:
            explicit_build_keys.add("domain_policies")
        object.__setattr__(runtime_config, "_explicit_keys", frozenset(explicit_build_keys))
        return runtime_config.validate()

    runtime_config = clone_ingest_config(normalized)
    explicit_keys: set[str] = set(runtime_config.explicit_keys())
    if mode is not None:
        runtime_config.mode = mode
        explicit_keys.add("mode")
    if strict is not None:
        runtime_config.strict = strict
        explicit_keys.add("strict")
    if allow_local_urls is not UNSET:
        runtime_config.allow_local_urls = allow_local_urls
        explicit_keys.add("allow_local_urls")
    if model is not None:
        runtime_config.model = model
        explicit_keys.add("model")
    if timeout is not None:
        runtime_config.timeout = timeout
        explicit_keys.add("timeout")
    if auto_render_threshold is not None:
        runtime_config.auto_render_threshold = auto_render_threshold
        explicit_keys.add("auto_render_threshold")
    if stealth is not None:
        runtime_config.stealth = stealth
        explicit_keys.add("stealth")
    if disable_http2 is not None:
        runtime_config.disable_http2 = disable_http2
        explicit_keys.add("disable_http2")
    if extreme_mode is not None:
        runtime_config.extreme_mode = extreme_mode
        explicit_keys.add("extreme_mode")
    if screenshot is not UNSET:
        runtime_config.screenshot = screenshot
        explicit_keys.add("screenshot")
    if extract_metadata is not None:
        runtime_config.extract_metadata = extract_metadata
        explicit_keys.add("extract_metadata")
    if extract_links is not None:
        runtime_config.extract_links = extract_links
        explicit_keys.add("extract_links")
    if advanced_security is not None:
        runtime_config.advanced_security = advanced_security
        explicit_keys.add("advanced_security")
    if use_llm is not None:
        runtime_config.use_llm = use_llm
        explicit_keys.add("use_llm")
    if cache is not UNSET:
        runtime_config.cache = cache
        explicit_keys.add("cache")
    if cache_ttl is not UNSET:
        runtime_config.cache_ttl = cache_ttl
        explicit_keys.add("cache_ttl")
    if policy_name is not None:
        runtime_config.policy_name = policy_name
        explicit_keys.add("policy_name")
    if custom_patterns is not None:
        runtime_config.custom_patterns = validated_custom_patterns
        explicit_keys.add("custom_patterns")
    if plugin_dirs is not None:
        runtime_config.plugin_dirs = validated_plugin_dirs
        explicit_keys.add("plugin_dirs")
    if output_format is not None:
        runtime_config.output_format = output_format
        explicit_keys.add("output_format")
    if output_profile is not None:
        runtime_config.output_profile = validated_output_profile or "default"
        explicit_keys.add("output_profile")
    if output_formats is not None:
        runtime_config.output_formats = output_formats
        explicit_keys.add("output_formats")
    if extract_blocks is not None:
        runtime_config.extract_blocks = extract_blocks
        explicit_keys.add("extract_blocks")
    if chunking_strategy is not None:
        runtime_config.chunking_strategy = chunking_strategy
        explicit_keys.add("chunking_strategy")
    if chunk_size is not None:
        runtime_config.chunk_size = chunk_size
        explicit_keys.add("chunk_size")
    if chunk_overlap is not None:
        runtime_config.chunk_overlap = chunk_overlap
        explicit_keys.add("chunk_overlap")
    if detect_language is not None:
        runtime_config.detect_language = detect_language
        explicit_keys.add("detect_language")
    if normalize_multilingual is not None:
        runtime_config.normalize_multilingual = normalize_multilingual
        explicit_keys.add("normalize_multilingual")
    if include_security_explanation is not None:
        runtime_config.include_security_explanation = include_security_explanation
        explicit_keys.add("include_security_explanation")
    if include_observability is not None:
        runtime_config.include_observability = include_observability
        explicit_keys.add("include_observability")
    if save_reports is not None:
        runtime_config.save_reports = save_reports
        explicit_keys.add("save_reports")
    if reports_dir is not None:
        runtime_config.reports_dir = reports_dir
        explicit_keys.add("reports_dir")
    if fetcher_user_agent is not None:
        runtime_config.fetcher_user_agent = fetcher_user_agent
        explicit_keys.add("fetcher_user_agent")
    if domain_request_interval is not None:
        runtime_config.domain_request_interval = domain_request_interval
        explicit_keys.add("domain_request_interval")
    if circuit_breaker_threshold is not None:
        runtime_config.circuit_breaker_threshold = circuit_breaker_threshold
        explicit_keys.add("circuit_breaker_threshold")
    if circuit_breaker_open_seconds is not None:
        runtime_config.circuit_breaker_open_seconds = circuit_breaker_open_seconds
        explicit_keys.add("circuit_breaker_open_seconds")
    if render_cost_budget is not UNSET:
        runtime_config.render_cost_budget = render_cost_budget
        explicit_keys.add("render_cost_budget")
    if domain_policies is not None:
        runtime_config.domain_policies = validated_domain_policies
        explicit_keys.add("domain_policies")

    object.__setattr__(runtime_config, "_explicit_keys", frozenset(explicit_keys))
    return runtime_config.validate()
