"""Runtime config helpers for the public API surface."""

from __future__ import annotations

from typing import Literal

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.config import Config as FileConfig

UNSET = object()


def normalize_runtime_config(config: IngestConfig | FileConfig | None) -> IngestConfig | None:
    """Accept both runtime and file-based config objects on the public API."""
    if config is None:
        return None
    if isinstance(config, FileConfig):
        return config.to_ingest_config()
    return config


def clone_ingest_config(config: IngestConfig) -> IngestConfig:
    """Copy a runtime config so concurrent callers do not mutate shared state."""
    return config.clone()


def build_runtime_config(
    config: IngestConfig | FileConfig | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
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
    output_profile: str | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    render_cost_budget: int | None = None,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
) -> IngestConfig:
    """Build an isolated runtime config from file/runtime config plus overrides."""
    normalized = normalize_runtime_config(config)

    if normalized is None:
        runtime_config = IngestConfig(
            mode=mode if mode is not None else "auto",
            strict=strict if strict is not None else True,
            model=model if model is not None else "gpt-4",
            timeout=timeout if timeout is not None else 30.0,
            auto_render_threshold=auto_render_threshold if auto_render_threshold is not None else 50,
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
            custom_patterns=custom_patterns or [],
            plugin_dirs=plugin_dirs or [],
            output_profile=output_profile if output_profile is not None else "default",
            extract_blocks=extract_blocks if extract_blocks is not None else False,
            chunking_strategy=chunking_strategy if chunking_strategy is not None else "none",
            chunk_size=chunk_size if chunk_size is not None else 1200,
            chunk_overlap=chunk_overlap if chunk_overlap is not None else 120,
            detect_language=detect_language if detect_language is not None else True,
            normalize_multilingual=normalize_multilingual if normalize_multilingual is not None else True,
            include_security_explanation=(
                include_security_explanation if include_security_explanation is not None else True
            ),
            include_observability=include_observability if include_observability is not None else True,
            render_cost_budget=render_cost_budget,
            domain_policies=[
                policy if isinstance(policy, DomainPolicy) else DomainPolicy(**policy)
                for policy in (domain_policies or [])
            ],
        )
        explicit: set[str] = set()
        if mode is not None:
            explicit.add("mode")
        if strict is not None:
            explicit.add("strict")
        if model is not None:
            explicit.add("model")
        if timeout is not None:
            explicit.add("timeout")
        if auto_render_threshold is not None:
            explicit.add("auto_render_threshold")
        if stealth is not None:
            explicit.add("stealth")
        if disable_http2 is not None:
            explicit.add("disable_http2")
        if extreme_mode is not None:
            explicit.add("extreme_mode")
        if screenshot is not UNSET:
            explicit.add("screenshot")
        if extract_metadata is not None:
            explicit.add("extract_metadata")
        if extract_links is not None:
            explicit.add("extract_links")
        if advanced_security is not None:
            explicit.add("advanced_security")
        if use_llm is not None:
            explicit.add("use_llm")
        if cache is not UNSET:
            explicit.add("cache")
        if cache_ttl is not UNSET:
            explicit.add("cache_ttl")
        if policy_name is not None:
            explicit.add("policy_name")
        if custom_patterns is not None:
            explicit.add("custom_patterns")
        if plugin_dirs is not None:
            explicit.add("plugin_dirs")
        if output_profile is not None:
            explicit.add("output_profile")
        if extract_blocks is not None:
            explicit.add("extract_blocks")
        if chunking_strategy is not None:
            explicit.add("chunking_strategy")
        if chunk_size is not None:
            explicit.add("chunk_size")
        if chunk_overlap is not None:
            explicit.add("chunk_overlap")
        if detect_language is not None:
            explicit.add("detect_language")
        if normalize_multilingual is not None:
            explicit.add("normalize_multilingual")
        if include_security_explanation is not None:
            explicit.add("include_security_explanation")
        if include_observability is not None:
            explicit.add("include_observability")
        if render_cost_budget is not None:
            explicit.add("render_cost_budget")
        if domain_policies is not None:
            explicit.add("domain_policies")
        object.__setattr__(runtime_config, "_explicit_keys", frozenset(explicit))
        return runtime_config

    runtime_config = clone_ingest_config(normalized)
    explicit: set[str] = set(runtime_config.explicit_keys())
    if mode is not None:
        runtime_config.mode = mode
        explicit.add("mode")
    if strict is not None:
        runtime_config.strict = strict
        explicit.add("strict")
    if model is not None:
        runtime_config.model = model
        explicit.add("model")
    if timeout is not None:
        runtime_config.timeout = timeout
        explicit.add("timeout")
    if auto_render_threshold is not None:
        runtime_config.auto_render_threshold = auto_render_threshold
        explicit.add("auto_render_threshold")
    if stealth is not None:
        runtime_config.stealth = stealth
        explicit.add("stealth")
    if disable_http2 is not None:
        runtime_config.disable_http2 = disable_http2
        explicit.add("disable_http2")
    if extreme_mode is not None:
        runtime_config.extreme_mode = extreme_mode
        explicit.add("extreme_mode")
    if screenshot is not UNSET:
        runtime_config.screenshot = screenshot
        explicit.add("screenshot")
    if extract_metadata is not None:
        runtime_config.extract_metadata = extract_metadata
        explicit.add("extract_metadata")
    if extract_links is not None:
        runtime_config.extract_links = extract_links
        explicit.add("extract_links")
    if advanced_security is not None:
        runtime_config.advanced_security = advanced_security
        explicit.add("advanced_security")
    if use_llm is not None:
        runtime_config.use_llm = use_llm
        explicit.add("use_llm")
    if cache is not UNSET:
        runtime_config.cache = cache
        explicit.add("cache")
    if cache_ttl is not UNSET:
        runtime_config.cache_ttl = cache_ttl
        explicit.add("cache_ttl")
    if policy_name is not None:
        runtime_config.policy_name = policy_name
        explicit.add("policy_name")
    if custom_patterns is not None:
        runtime_config.custom_patterns = custom_patterns
        explicit.add("custom_patterns")
    if plugin_dirs is not None:
        runtime_config.plugin_dirs = plugin_dirs
        explicit.add("plugin_dirs")
    if output_profile is not None:
        runtime_config.output_profile = output_profile
        explicit.add("output_profile")
    if extract_blocks is not None:
        runtime_config.extract_blocks = extract_blocks
        explicit.add("extract_blocks")
    if chunking_strategy is not None:
        runtime_config.chunking_strategy = chunking_strategy
        explicit.add("chunking_strategy")
    if chunk_size is not None:
        runtime_config.chunk_size = chunk_size
        explicit.add("chunk_size")
    if chunk_overlap is not None:
        runtime_config.chunk_overlap = chunk_overlap
        explicit.add("chunk_overlap")
    if detect_language is not None:
        runtime_config.detect_language = detect_language
        explicit.add("detect_language")
    if normalize_multilingual is not None:
        runtime_config.normalize_multilingual = normalize_multilingual
        explicit.add("normalize_multilingual")
    if include_security_explanation is not None:
        runtime_config.include_security_explanation = include_security_explanation
        explicit.add("include_security_explanation")
    if include_observability is not None:
        runtime_config.include_observability = include_observability
        explicit.add("include_observability")
    if render_cost_budget is not None:
        runtime_config.render_cost_budget = render_cost_budget
        explicit.add("render_cost_budget")
    if domain_policies is not None:
        runtime_config.domain_policies = [
            policy if isinstance(policy, DomainPolicy) else DomainPolicy(**policy)
            for policy in domain_policies
        ]
        explicit.add("domain_policies")

    object.__setattr__(runtime_config, "_explicit_keys", frozenset(explicit))
    return runtime_config.validate()
