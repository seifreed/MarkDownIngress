"""Helpers for turning fetched HTML into the final SafeDocument."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.document_assembly import (
    _build_safe_document,
    _SafeDocumentAssemblyContext,
)
from markdown_ingress.core.document_plugins import (
    DocumentPluginContext,
    create_document_plugin_context,
    load_document_plugins,
    unload_document_plugins,
)
from markdown_ingress.core.document_policy import build_policy_engine
from markdown_ingress.core.document_security_patterns import (
    _apply_custom_pattern_analysis,
)
from markdown_ingress.core.document_security_patterns import (
    _dedupe_preserving_order as _dedupe_preserving_order,
)
from markdown_ingress.core.domain_rules import apply_domain_html_rules
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.ingest_stats import timed_stage_with_snapshot
from markdown_ingress.core.interfaces import IExtractor, IMarkdownConverter, ITokenEstimator
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.core.scoring import Scorer
from markdown_ingress.core.security_engine import SecurityEngine
from markdown_ingress.core.structured import (
    ChunkBuilder,
    HTMLStructureExtractor,
)
from markdown_ingress.models import FetchResult, SafeDocument

_extractor_factory: Callable[[bool], IExtractor] | None = None
_md_converter_factory: Callable[[], IMarkdownConverter] | None = None
_token_estimator_factory: Callable[[str], ITokenEstimator] | None = None


@dataclass(frozen=True)
class _ContentPipelineContext:
    extractor: IExtractor
    md_converter: IMarkdownConverter
    structure_extractor: Any
    chunk_builder: Any
    orchestrator: Any
    fetch_result: FetchResult
    config: IngestConfig
    matched_domain_policy: DomainPolicy | None
    operational_flags: list[str]
    stage_timings: dict[str, float]
    document_url: str


def register_document_builder_factories(
    extractor_factory: Callable[[bool], IExtractor],
    md_converter_factory: Callable[[], IMarkdownConverter],
    token_estimator_factory: Callable[[str], ITokenEstimator],
) -> None:
    global _extractor_factory, _md_converter_factory, _token_estimator_factory
    _extractor_factory = extractor_factory
    _md_converter_factory = md_converter_factory
    _token_estimator_factory = token_estimator_factory


def _resolve_pipeline_dependencies(orchestrator, config: IngestConfig):
    """Return (extractor, md_converter, hasher, token_estimator, scorer) for the pipeline."""
    if orchestrator.extractor is None:
        if _extractor_factory is None:
            raise RuntimeError(
                "No extractor factory registered — call register_document_builder_factories()."
            )
        extractor: IExtractor = _extractor_factory(config.strict)
    else:
        extractor = orchestrator.extractor
    if orchestrator.md_converter is None:
        if _md_converter_factory is None:
            raise RuntimeError("No md_converter factory registered.")
        md_converter: IMarkdownConverter = _md_converter_factory()
    else:
        md_converter = orchestrator.md_converter
    hasher = orchestrator.hasher or Hasher()
    if orchestrator.token_estimator is None:
        if _token_estimator_factory is None:
            raise RuntimeError("No token_estimator factory registered.")
        token_estimator: ITokenEstimator = _token_estimator_factory(config.model)
    else:
        token_estimator = orchestrator.token_estimator
    scorer = orchestrator.scorer or Scorer()
    return extractor, md_converter, hasher, token_estimator, scorer


def _extract_and_apply_domain_rules(
    extractor: IExtractor,
    fetch_result: FetchResult,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    stage_timings: dict[str, float],
):
    """Extract HTML content and apply domain-level filtering rules."""
    extraction_result = timed_stage_with_snapshot(
        stage_timings,
        "extract",
        lambda: extractor.extract(fetch_result.html, fetch_result.url),
    )
    filtered_html, domain_rule_stats = apply_domain_html_rules(
        extraction_result.html, matched_domain_policy
    )
    if filtered_html != extraction_result.html:
        extraction_result.html = filtered_html
        re_extracted = timed_stage_with_snapshot(
            stage_timings,
            "domain_rules_text",
            lambda: extractor.extract(filtered_html, fetch_result.url),
        )
        extraction_result.text_content = re_extracted.text_content
        extraction_result.title = re_extracted.title
        extraction_result.author = re_extracted.author
        extraction_result.removed_hidden += re_extracted.removed_hidden
        for tag, count in re_extracted.removed_tags.items():
            extraction_result.removed_tags[tag] = extraction_result.removed_tags.get(tag, 0) + count
        if any(domain_rule_stats.values()):
            operational_flags.append("domain_rules_applied")
    return extraction_result, domain_rule_stats


def _enrich_metadata_and_links(context: _ContentPipelineContext, extraction_result):
    """Optionally extract enriched page metadata and outbound links."""
    config = context.config
    enriched_metadata = None
    if config.extract_metadata:
        metadata_extractor = context.orchestrator.metadata_extractor or MetadataExtractor()
        enriched_metadata = timed_stage_with_snapshot(
            context.stage_timings,
            "metadata",
            lambda: metadata_extractor.extract(
                context.fetch_result.html,
                context.document_url,
                detect_language=config.detect_language,
                normalize_multilingual=config.normalize_multilingual,
            ),
        )
    links = None
    if config.extract_links:
        link_analyzer = context.orchestrator.link_analyzer or LinkAnalyzer()
        links = timed_stage_with_snapshot(
            context.stage_timings,
            "links",
            lambda: link_analyzer.analyze(extraction_result.html, context.document_url),
        )
    return enriched_metadata, links


def _extract_structure(
    structure_extractor,
    chunk_builder,
    config: IngestConfig,
    extraction_result,
    stage_timings: dict[str, float],
):
    """Conditionally extract structural blocks and semantic chunks from the HTML."""
    chunking_explicit = "chunking_strategy" in config.explicit_keys()
    chunks_requested = config.chunking_strategy != "none" and (
        config.extract_blocks or "chunks" in config.output_formats or chunking_explicit
    )
    extracted_blocks = []
    if config.extract_blocks or chunks_requested:
        extracted_blocks = timed_stage_with_snapshot(
            stage_timings,
            "blocks",
            lambda: structure_extractor.extract(extraction_result.html),
        )
    chunks = []
    if extracted_blocks and chunks_requested:
        chunks = timed_stage_with_snapshot(
            stage_timings,
            "chunking",
            lambda: chunk_builder.build(
                extracted_blocks,
                strategy=config.chunking_strategy,
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,
            ),
        )
    structured_blocks = extracted_blocks if config.extract_blocks else []
    return structured_blocks, chunks, chunks_requested


def _run_content_pipeline(context: _ContentPipelineContext):
    """Extract, normalise, and structure the fetched HTML. Returns pipeline outputs."""
    extraction_result, domain_rule_stats = _extract_and_apply_domain_rules(
        context.extractor,
        context.fetch_result,
        context.matched_domain_policy,
        context.operational_flags,
        context.stage_timings,
    )
    enriched_metadata, links = _enrich_metadata_and_links(context, extraction_result)
    markdown = timed_stage_with_snapshot(
        context.stage_timings,
        "markdown",
        lambda: context.md_converter.convert(extraction_result.html),
    )
    structured_blocks, chunks, chunks_requested = _extract_structure(
        context.structure_extractor,
        context.chunk_builder,
        context.config,
        extraction_result,
        context.stage_timings,
    )
    return (
        extraction_result,
        markdown,
        structured_blocks,
        chunks,
        enriched_metadata,
        links,
        context.operational_flags,
        domain_rule_stats,
        chunks_requested,
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


def _cleanup_screenshot_on_failure(
    document: SafeDocument | None,
    fetch_result: FetchResult,
) -> None:
    """Remove a temporary screenshot file when document creation failed."""
    if document is None and fetch_result.metadata.get("screenshot_temp"):
        path = fetch_result.metadata.get("screenshot_path")
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


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
    ) = _run_content_pipeline(
        _ContentPipelineContext(
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
                security_result,
                extraction_result,
                security_metadata,
                security_engine,
                policy_engine,
                config,
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
        _cleanup_screenshot_on_failure(document, fetch_result)
