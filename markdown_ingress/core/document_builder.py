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
from markdown_ingress.core.document_security_patterns import (
    _dedupe_preserving_order as _dedupe_preserving_order,
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


def process_fetched_content(
    orchestrator,
    fetch_result: FetchResult,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
    operational_flags: list[str] | None = None,
) -> SafeDocument:
    """Transform already-fetched HTML into the final SafeDocument."""
    plugin_context: DocumentPluginContext | None = None
    document: SafeDocument | None = None

    extractor, md_converter, hasher, token_estimator, scorer = _resolve_pipeline_dependencies(
        orchestrator, config
    )
    structure_extractor = HTMLStructureExtractor(hasher=hasher)
    chunk_builder = ChunkBuilder(hasher=hasher, token_estimator=token_estimator)
    stage_timings: dict[str, float] = dict(fetch_result.metadata.get("stage_timings_ms", {}))
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
    ) = run_content_pipeline(
        ContentPipelineContext(
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

    security_metadata = {"hidden_elements_count": extraction_result.removed_hidden}
    security_engine, policy_engine, security_result = _run_security_analysis(
        config, extraction_result, security_metadata, stage_timings, matched_domain_policy
    )

    plugin_context = create_document_plugin_context(config.custom_patterns)
    try:
        load_document_plugins(plugin_context, config.plugin_dirs)

        if plugin_context.extra_patterns:
            security_result = _apply_custom_pattern_analysis(
                plugin_context.extra_patterns,
                CustomPatternAnalysisContext(
                    security_result=security_result,
                    extraction_result=extraction_result,
                    security_metadata=security_metadata,
                    security_engine=security_engine,
                    policy_engine=policy_engine,
                    config=config,
                ),
            )

        try:
            document = _build_safe_document(
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
        except PolicyBlockedError as exc:
            if exc.document is not None:
                document = exc.document
            raise
        return document
    finally:
        plugin_loader = plugin_context.loader if plugin_context is not None else None
        unload_document_plugins(plugin_loader, document, fetch_result)
        cleanup_screenshot_on_failure(document, fetch_result)
