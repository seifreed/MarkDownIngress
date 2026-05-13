"""Adapt legacy Config instances into runtime ingest configuration."""

from __future__ import annotations

from typing import Any

from markdown_ingress.config_models import IngestConfig, _normalize_domain_policies
from markdown_ingress.config_validation import validate_string_list

_CONFIG_TO_RUNTIME_KEYS: dict[str, tuple[str, ...]] = {
    "mode": ("mode",),
    "timeout": ("timeout",),
    "auto_render_threshold": ("auto_render_threshold",),
    "strict": ("strict",),
    "allow_local_urls": ("allow_local_urls",),
    "model": ("model",),
    "cache_enabled": ("cache", "cache_ttl"),
    "cache_ttl": ("cache_ttl",),
    "policy": ("policy_name",),
    "custom_patterns": ("custom_patterns",),
    "plugin_dirs": ("plugin_dirs",),
    "domain_policies": ("domain_policies",),
    "output_format": ("output_format",),
    "output_profile": ("output_profile",),
    "output_formats": ("output_formats",),
    "extract_blocks": ("extract_blocks",),
    "extract_metadata": ("extract_metadata",),
    "extract_links": ("extract_links",),
    "advanced_security": ("advanced_security",),
    "use_llm": ("use_llm",),
    "detect_language": ("detect_language",),
    "normalize_multilingual": ("normalize_multilingual",),
    "include_security_explanation": ("include_security_explanation",),
    "chunking_strategy": ("chunking_strategy",),
    "chunk_size": ("chunk_size",),
    "chunk_overlap": ("chunk_overlap",),
    "save_reports": ("save_reports",),
    "reports_dir": ("reports_dir",),
    "stealth": ("stealth",),
    "disable_http2": ("disable_http2",),
    "extreme_mode": ("extreme_mode",),
    "screenshot": ("screenshot",),
    "batch_timeout": ("batch_timeout",),
    "batch_max_concurrent": ("batch_max_concurrent",),
    "render_cost_budget": ("render_cost_budget",),
    "include_observability": ("include_observability",),
    "fetcher_user_agent": ("fetcher_user_agent",),
    "domain_request_interval": ("domain_request_interval",),
    "circuit_breaker_threshold": ("circuit_breaker_threshold",),
    "circuit_breaker_open_seconds": ("circuit_breaker_open_seconds",),
}


def build_ingest_config(config: Any) -> IngestConfig:
    """Convert a legacy Config-like object into the runtime IngestConfig."""
    ingest_config = IngestConfig(**_build_ingest_config_kwargs(config))
    object.__setattr__(
        ingest_config,
        "_explicit_keys",
        _map_explicit_runtime_keys(config.explicit_keys()),
    )
    return ingest_config


def _build_ingest_config_kwargs(config: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        mode=config.mode,
        strict=config.strict,
        model=config.model,
        timeout=config.timeout,
        auto_render_threshold=config.auto_render_threshold,
        cache=config.create_cache(),
        cache_ttl=config.cache_ttl if config.cache_enabled else None,
        allow_local_urls=config.allow_local_urls,
        policy_name=config.normalized_policy(),
        custom_patterns=validate_string_list("custom_patterns", config.custom_patterns),
        plugin_dirs=validate_string_list("plugin_dirs", config.plugin_dirs),
        domain_policies=_normalize_domain_policies(config.domain_policies),
        output_format=config.output_format,
        output_profile=config.output_profile,
        output_formats=validate_string_list("output_formats", config.output_formats),
        extract_blocks=config.extract_blocks,
        extract_metadata=config.extract_metadata,
        extract_links=config.extract_links,
        advanced_security=config.advanced_security,
        use_llm=config.use_llm,
        detect_language=config.detect_language,
        normalize_multilingual=config.normalize_multilingual,
        include_security_explanation=config.include_security_explanation,
        chunking_strategy=config.chunking_strategy,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        save_reports=config.save_reports,
        reports_dir=config.reports_dir,
        render_cost_budget=config.render_cost_budget,
        include_observability=config.include_observability,
        fetcher_user_agent=config.fetcher_user_agent,
        domain_request_interval=config.domain_request_interval,
        circuit_breaker_threshold=config.circuit_breaker_threshold,
        circuit_breaker_open_seconds=config.circuit_breaker_open_seconds,
        batch_timeout=config.batch_timeout,
        batch_max_concurrent=config.batch_max_concurrent,
    )
    for render_field in ("stealth", "disable_http2", "extreme_mode", "screenshot"):
        if hasattr(config, render_field):
            kwargs[render_field] = getattr(config, render_field)
    return kwargs


def _map_explicit_runtime_keys(explicit_config_keys: frozenset[str]) -> frozenset[str]:
    explicit_runtime_keys: set[str] = set()
    for config_key in explicit_config_keys:
        explicit_runtime_keys.update(_CONFIG_TO_RUNTIME_KEYS.get(config_key, ()))
    return frozenset(explicit_runtime_keys)
