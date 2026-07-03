"""Helpers for turning fetched HTML into the final SafeDocument."""

from __future__ import annotations

from collections.abc import Callable

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.document_assembly import (
    _build_safe_document,
    _SafeDocumentAssemblyContext,
)
from markdown_ingress.core.document_cleanup import cleanup_screenshot_on_failure
from markdown_ingress.core.document_content_pipeline import (
    ContentPipelineContext,
    run_content_pipeline,
)
from markdown_ingress.core.document_dependencies import (
    register_document_builder_factories as _register_document_builder_factories,
)
from markdown_ingress.core.document_dependencies import (
    resolve_pipeline_dependencies as _resolve_pipeline_dependencies,
)
from markdown_ingress.core.document_plugins import (
    DocumentPluginContext,
    create_document_plugin_context,
    load_document_plugins,
    unload_document_plugins,
)
from markdown_ingress.core.document_policy import build_policy_engine
from markdown_ingress.core.document_security_patterns import (
    CustomPatternAnalysisContext,
    _apply_custom_pattern_analysis,
)
from markdown_ingress.core.ingest_stats import timed_stage_with_snapshot
from markdown_ingress.core.interfaces import IExtractor, IMarkdownConverter, ITokenEstimator
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.core.security_engine import SecurityEngine
from markdown_ingress.core.structured import (
    ChunkBuilder,
    HTMLStructureExtractor,
)
from markdown_ingress.models import FetchResult, SafeDocument


def register_document_builder_factories(
    extractor_factory: Callable[[bool], IExtractor],
    md_converter_factory: Callable[[], IMarkdownConverter],
    token_estimator_factory: Callable[[str], ITokenEstimator],
) -> None:
    _register_document_builder_factories(
        extractor_factory,
        md_converter_factory,
        token_estimator_factory,
    )


def _run_security_analysis(
    config: IngestConfig,
    extraction_result,
    security_metadata: dict,
    stage_timings: dict[str, float],
    matched_domain_policy: DomainPolicy | None,
) -> tuple:
    """Run security and policy analysis on extracted content."""
    security_engine = SecurityEngine(
        strict=config.strict,
        advanced_security=config.advanced_security,
        use_llm=config.use_llm,
    )
    policy_engine = build_policy_engine(config.policy_name, matched_domain_policy)
    security_result = timed_stage_with_snapshot(
        stage_timings,
        "security",
        lambda: security_engine.analyze(
            extraction_result.text_content,
            security_metadata,
            block_threshold=policy_engine.policy.block_threshold,
            warn_threshold=policy_engine.policy.warn_threshold,
        ),
    )
    return security_engine, policy_engine, security_result


def _build_content_pipeline_context(
    *,
    extractor,
    md_converter,
    structure_extractor,
    chunk_builder,
    orchestrator,
    fetch_result: FetchResult,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    stage_timings: dict[str, float],
    document_url: str,
) -> ContentPipelineContext:
    return ContentPipelineContext(
        extractor=extractor,
        md_converter=md_converter,
        structure_extractor=structure_extractor,
        chunk_builder=chunk_builder,
        orchestrator=orchestrator,
        fetch_result=fetch_result,
        config=config,
        matched_domain_policy=matched_domain_policy,
        operational_flags=operational_flags,
        stage_timings=stage_timings,
        document_url=document_url,
    )


def _run_content_pipeline(context: ContentPipelineContext):
    return run_content_pipeline(context)


def _apply_custom_patterns_if_any(
    context: CustomPatternAnalysisContext,
    extra_patterns,
) -> dict:
    if not extra_patterns:
        return context.security_result
    return _apply_custom_pattern_analysis(extra_patterns, context)


def _build_document(
    *,
    config: IngestConfig,
    fetch_result: FetchResult,
    extraction_result,
    markdown: str,
    structured_blocks,
    chunks,
    chunks_requested: bool,
    enriched_metadata: dict,
    links,
    security_result: dict,
    token_estimator,
    hasher,
    scorer,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    domain_rule_stats,
    plugin_context: DocumentPluginContext,
    stage_timings: dict[str, float],
    policy_engine,
) -> SafeDocument:
    return _build_safe_document(
        _SafeDocumentAssemblyContext(
            config=config,
            fetch_result=fetch_result,
            extraction_result=extraction_result,
            markdown=markdown,
            structured_blocks=structured_blocks,
            chunks=chunks,
            chunks_requested=chunks_requested,
            enriched_metadata=enriched_metadata,
            links=links,
            security_result=security_result,
            token_estimator=token_estimator,
            hasher=hasher,
            scorer=scorer,
            matched_domain_policy=matched_domain_policy,
            operational_flags=operational_flags,
            domain_rule_stats=domain_rule_stats,
            extra_patterns=plugin_context.extra_patterns,
            plugins_loaded=plugin_context.plugins_loaded,
            stage_timings=stage_timings,
            policy_engine=policy_engine,
        )
    )


def _prepare_pipeline_components(orchestrator, fetch_result: FetchResult, config: IngestConfig):
    extractor, md_converter, hasher, token_estimator, scorer = _resolve_pipeline_dependencies(
        orchestrator, config
    )
    stage_timings: dict[str, float] = dict(fetch_result.metadata.get("stage_timings_ms", {}))
    return (
        extractor,
        md_converter,
        hasher,
        token_estimator,
        scorer,
        HTMLStructureExtractor(hasher=hasher),
        ChunkBuilder(hasher=hasher, token_estimator=token_estimator),
        stage_timings,
    )


def _run_content_stages(
    *,
    extractor,
    md_converter,
    structure_extractor,
    chunk_builder,
    orchestrator,
    fetch_result: FetchResult,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    stage_timings: dict[str, float],
    document_url: str,
):
    return _run_content_pipeline(
        _build_content_pipeline_context(
            extractor=extractor,
            md_converter=md_converter,
            structure_extractor=structure_extractor,
            chunk_builder=chunk_builder,
            orchestrator=orchestrator,
            fetch_result=fetch_result,
            config=config,
            matched_domain_policy=matched_domain_policy,
            operational_flags=operational_flags,
            stage_timings=stage_timings,
            document_url=document_url,
        )
    )


def _build_security_context(
    security_result: dict,
    extraction_result,
    security_metadata: dict,
    security_engine: SecurityEngine,
    policy_engine,
    config: IngestConfig,
) -> CustomPatternAnalysisContext:
    return CustomPatternAnalysisContext(
        security_result=security_result,
        extraction_result=extraction_result,
        security_metadata=security_metadata,
        security_engine=security_engine,
        policy_engine=policy_engine,
        config=config,
    )


def _build_document_with_plugins(
    *,
    config: IngestConfig,
    fetch_result: FetchResult,
    extraction_result,
    markdown: str,
    structured_blocks,
    chunks,
    chunks_requested: bool,
    enriched_metadata: dict,
    links,
    security_context: CustomPatternAnalysisContext,
    token_estimator,
    hasher,
    scorer,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    domain_rule_stats,
    stage_timings: dict[str, float],
    policy_engine,
) -> SafeDocument:
    plugin_context = create_document_plugin_context(config.custom_patterns)
    document: SafeDocument | None = None
    try:
        load_document_plugins(plugin_context, config.plugin_dirs)
        security_result = _apply_custom_patterns_if_any(
            security_context,
            plugin_context.extra_patterns,
        )
        document = _build_document(
            config=config,
            fetch_result=fetch_result,
            extraction_result=extraction_result,
            markdown=markdown,
            structured_blocks=structured_blocks,
            chunks=chunks,
            chunks_requested=chunks_requested,
            enriched_metadata=enriched_metadata,
            links=links,
            security_result=security_result,
            token_estimator=token_estimator,
            hasher=hasher,
            scorer=scorer,
            matched_domain_policy=matched_domain_policy,
            operational_flags=operational_flags,
            domain_rule_stats=domain_rule_stats,
            plugin_context=plugin_context,
            stage_timings=stage_timings,
            policy_engine=policy_engine,
        )
        return document
    except PolicyBlockedError as exc:
        if exc.document is not None:
            document = exc.document
        raise
    finally:
        plugin_loader = plugin_context.loader if plugin_context is not None else None
        unload_document_plugins(plugin_loader, document, fetch_result)
        cleanup_screenshot_on_failure(document, fetch_result)


def process_fetched_content(
    orchestrator,
    fetch_result: FetchResult,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
    operational_flags: list[str] | None = None,
) -> SafeDocument:
    """Transform already-fetched HTML into the final SafeDocument."""
    (
        extractor,
        md_converter,
        hasher,
        token_estimator,
        scorer,
        structure_extractor,
        chunk_builder,
        stage_timings,
    ) = _prepare_pipeline_components(orchestrator, fetch_result, config)
    operational_flags = list(operational_flags or [])
    document_url = fetch_result.final_url or fetch_result.url

    (
        extraction_result,
        markdown,
        structured_blocks,
        chunks,
        enriched_metadata,
        links,
        operational_flags,
        domain_rule_stats,
        chunks_requested,
    ) = _run_content_stages(
        extractor=extractor,
        md_converter=md_converter,
        structure_extractor=structure_extractor,
        chunk_builder=chunk_builder,
        orchestrator=orchestrator,
        fetch_result=fetch_result,
        config=config,
        matched_domain_policy=matched_domain_policy,
        operational_flags=operational_flags,
        stage_timings=stage_timings,
        document_url=document_url,
    )

    security_metadata = {"hidden_elements_count": extraction_result.removed_hidden}
    security_engine, policy_engine, security_result = _run_security_analysis(
        config, extraction_result, security_metadata, stage_timings, matched_domain_policy
    )

    security_context = _build_security_context(
        security_result=security_result,
        extraction_result=extraction_result,
        security_metadata=security_metadata,
        security_engine=security_engine,
        policy_engine=policy_engine,
        config=config,
    )

    return _build_document_with_plugins(
        config=config,
        fetch_result=fetch_result,
        extraction_result=extraction_result,
        markdown=markdown,
        structured_blocks=structured_blocks,
        chunks=chunks,
        chunks_requested=chunks_requested,
        enriched_metadata=enriched_metadata,
        links=links,
        security_context=security_context,
        token_estimator=token_estimator,
        hasher=hasher,
        scorer=scorer,
        matched_domain_policy=matched_domain_policy,
        operational_flags=operational_flags,
        domain_rule_stats=domain_rule_stats,
        stage_timings=stage_timings,
        policy_engine=policy_engine,
    )
