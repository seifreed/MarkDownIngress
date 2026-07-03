"""Ingestion orchestration entrypoint for the MarkDownIngress pipeline."""

import time
from typing import Any, TypedDict, Unpack, cast

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.document_builder import process_fetched_content
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.inflight import (
    InFlightRegistry,
    acquire_inflight,
    await_inflight,
    clone_cached_document,
    inflight_active_count,
    make_request_key,
    release_inflight,
)
from markdown_ingress.core.ingest_stats import (
    record_stage_timing,
    snapshot_ingest_stats,
)
from markdown_ingress.core.ingest_stats import (
    reset_ingest_stats as _reset_ingest_stats,
)
from markdown_ingress.core.interfaces import (
    IExtractor,
    IMarkdownConverter,
    INormalizer,
    ITokenEstimator,
)
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.core.scoring import Scorer
from markdown_ingress.models import FetchResult, SafeDocument


def get_ingest_stats() -> dict[str, Any]:
    """Return aggregate in-process ingestion stats for observability."""
    stats = snapshot_ingest_stats()
    stats["inflight_active"] = inflight_active_count()
    return stats


def reset_ingest_stats() -> None:
    """Reset aggregate in-process ingestion stats."""
    _reset_ingest_stats()


class OrchestratorOptions(TypedDict, total=False):
    extractor: IExtractor | None
    normalizer: INormalizer | None
    md_converter: IMarkdownConverter | None
    hasher: Hasher | None
    token_estimator: ITokenEstimator | None
    scorer: Scorer | None
    metadata_extractor: MetadataExtractor | None
    link_analyzer: LinkAnalyzer | None
    inflight_registry: InFlightRegistry | None


_ORCHESTRATOR_OPTION_NAMES = (
    "extractor",
    "normalizer",
    "md_converter",
    "hasher",
    "token_estimator",
    "scorer",
    "metadata_extractor",
    "link_analyzer",
    "inflight_registry",
)
_ORCHESTRATOR_OPTION_NAME_SET = frozenset(_ORCHESTRATOR_OPTION_NAMES)


def _normalize_orchestrator_options(
    args: tuple[object, ...],
    options: OrchestratorOptions,
) -> OrchestratorOptions:
    if len(args) > len(_ORCHESTRATOR_OPTION_NAMES):
        raise TypeError(
            f"IngestOrchestrator() expected at most {len(_ORCHESTRATOR_OPTION_NAMES)} arguments"
        )

    unexpected = set(options) - _ORCHESTRATOR_OPTION_NAME_SET
    if unexpected:
        name = sorted(unexpected)[0]
        raise TypeError(f"IngestOrchestrator() got an unexpected keyword argument '{name}'")

    normalized = dict(options)
    for index, value in enumerate(args):
        name = _ORCHESTRATOR_OPTION_NAMES[index]
        if name in normalized:
            raise TypeError(f"IngestOrchestrator() got multiple values for argument '{name}'")
        normalized[name] = value
    return cast(OrchestratorOptions, normalized)


class IngestOrchestrator:
    """
    Orchestrates the web → markdown ingestion pipeline.

    Coordinates fetching, extraction, conversion, and security analysis
    using dependency injection pattern for better testability and maintainability.
    """

    def __init__(self, *args: object, **options: Unpack[OrchestratorOptions]) -> None:
        """
        Initialize orchestrator with optional dependency injection.

        Args:
            extractor: HTML content extractor (implements IExtractor)
            normalizer: HTML normalizer (implements INormalizer)
            md_converter: Markdown converter
            hasher: Content hasher
            token_estimator: Token estimator
            scorer: Security scorer
            metadata_extractor: Metadata extractor
            link_analyzer: Link analyzer
        """
        parsed = _normalize_orchestrator_options(args, options)
        extractor = parsed.get("extractor")
        normalizer = parsed.get("normalizer")
        md_converter = parsed.get("md_converter")
        hasher = parsed.get("hasher")
        token_estimator = parsed.get("token_estimator")
        scorer = parsed.get("scorer")
        metadata_extractor = parsed.get("metadata_extractor")
        link_analyzer = parsed.get("link_analyzer")
        inflight_registry = parsed.get("inflight_registry")

        self.extractor = extractor
        self.normalizer = normalizer
        self.md_converter: IMarkdownConverter | None = md_converter
        self.hasher = hasher
        self.token_estimator: ITokenEstimator | None = token_estimator
        self.scorer = scorer
        self.metadata_extractor = metadata_extractor
        self.link_analyzer = link_analyzer
        self._inflight_registry_was_injected = inflight_registry is not None
        self.inflight_registry = inflight_registry or InFlightRegistry()
        if not self._inflight_registry_was_injected:
            self.inflight_registry.start_periodic_cleanup()
        self._default_inflight_registry = (
            self.inflight_registry if not self._inflight_registry_was_injected else None
        )

    @staticmethod
    def make_request_key(
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None = None,
    ) -> str:
        return make_request_key(url, config, matched_domain_policy)

    def acquire_inflight(self, request_key: str):
        return acquire_inflight(request_key, registry=self.inflight_registry)

    def await_inflight(self, entry, request_key: str):
        return await_inflight(entry, request_key, registry=self.inflight_registry)

    def release_inflight(self, request_key: str, *, document=None, error=None) -> int:
        return release_inflight(
            request_key,
            document=document,
            error=error,
            registry=self.inflight_registry,
        )

    clone_cached_document = staticmethod(clone_cached_document)

    def timed_stage(self, stage: str, fn):
        """Execute a stage and record aggregate timing."""
        started = time.perf_counter()
        try:
            return fn()
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            record_stage_timing(stage, duration_ms)

    def process_fetched_content(
        self,
        fetch_result: FetchResult,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None = None,
        operational_flags: list[str] | None = None,
    ) -> SafeDocument:
        """Transform already-fetched HTML into the final SafeDocument."""
        return process_fetched_content(
            self,
            fetch_result,
            config,
            matched_domain_policy=matched_domain_policy,
            operational_flags=operational_flags,
        )
