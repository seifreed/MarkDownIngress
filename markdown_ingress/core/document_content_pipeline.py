"""Content extraction, metadata, markdown, and structure stages for documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.domain_rules import apply_domain_html_rules
from markdown_ingress.core.ingest_stats import timed_stage_with_snapshot
from markdown_ingress.core.interfaces import IExtractor, IMarkdownConverter
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.models import FetchResult


@dataclass(frozen=True)
class ContentPipelineContext:
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


def run_content_pipeline(context: ContentPipelineContext):
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


def _extract_and_apply_domain_rules(
    extractor: IExtractor,
    fetch_result: FetchResult,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    stage_timings: dict[str, float],
):
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


def _enrich_metadata_and_links(context: ContentPipelineContext, extraction_result):
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
