"""Ingestion orchestration entrypoint for the MarkDownIngress pipeline."""

import time
from typing import Any, Final, Literal

from markdown_ingress.adapters.rendering.playwright_renderer import (
    PLAYWRIGHT_AVAILABLE,
)
from markdown_ingress.adapters.rendering.playwright_renderer import (
    PlaywrightRenderer as Renderer,
)
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
from markdown_ingress.core.interfaces import IExtractor, INormalizer
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.markdown import MarkdownConverter
from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.core.scoring import Scorer
from markdown_ingress.core.tokens import TokenEstimator
from markdown_ingress.models import FetchResult, SafeDocument

_SCREENSHOT_UNSET: Final = object()


def get_ingest_stats() -> dict[str, Any]:
    """Return aggregate in-process ingestion stats for observability."""
    stats = snapshot_ingest_stats()
    stats["inflight_active"] = inflight_active_count()
    return stats


def reset_ingest_stats() -> None:
    """Reset aggregate in-process ingestion stats."""
    _reset_ingest_stats()


class IngestOrchestrator:
    """
    Orchestrates the web → markdown ingestion pipeline.

    Coordinates fetching, extraction, conversion, and security analysis
    using dependency injection pattern for better testability and maintainability.
    """

    def __init__(
        self,
        extractor: IExtractor | None = None,
        normalizer: INormalizer | None = None,
        md_converter: MarkdownConverter | None = None,
        hasher: Hasher | None = None,
        token_estimator: TokenEstimator | None = None,
        scorer: Scorer | None = None,
        metadata_extractor: MetadataExtractor | None = None,
        link_analyzer: LinkAnalyzer | None = None,
        inflight_registry: InFlightRegistry | None = None,
    ):
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
        self.extractor = extractor
        self.normalizer = normalizer
        self.md_converter = md_converter
        self.hasher = hasher
        self.token_estimator = token_estimator
        self.scorer = scorer
        self.metadata_extractor = metadata_extractor
        self.link_analyzer = link_analyzer
        self._inflight_registry_was_injected = inflight_registry is not None
        self.inflight_registry = inflight_registry or InFlightRegistry()
        self._default_inflight_registry = (
            self.inflight_registry if not self._inflight_registry_was_injected else None
        )

    def execute(
        self,
        url: str,
        config: IngestConfig | None = None,
        # Backward compatibility: accept individual parameters
        mode: Literal["fast", "render", "auto"] | None = None,
        strict: bool | None = None,
        model: str | None = None,
        timeout: float | None = None,
        stealth: bool | None = None,
        disable_http2: bool | None = None,
        extreme_mode: bool | None = None,
        screenshot: bool | str | None = _SCREENSHOT_UNSET,  # type: ignore[assignment]
        extract_metadata: bool | None = None,
        extract_links: bool | None = None,
        advanced_security: bool | None = None,
        use_llm: bool | None = None,
    ) -> SafeDocument:
        """
        Execute the complete ingestion pipeline.

        Args:
            url: Target URL to ingest
            config: IngestConfig object with all settings (recommended)
            mode: Fetching mode (deprecated, use config)
            strict: Enable strict security mode (deprecated, use config)
            model: LLM model name for token estimation (deprecated, use config)
            timeout: Request timeout in seconds (deprecated, use config)
            stealth: Enable stealth mode (deprecated, use config)
            disable_http2: Disable HTTP/2 protocol (deprecated, use config)
            extreme_mode: Enable extreme timeouts (deprecated, use config)
            screenshot: Screenshot configuration (deprecated, use config)
            extract_metadata: Extract enriched metadata (deprecated, use config)
            extract_links: Extract and analyze links (deprecated, use config)
            advanced_security: Enable Nova-tracer detection (deprecated, use config)
            use_llm: Enable LLM-based detection (deprecated, use config)

        Returns:
            SafeDocument with markdown content, metadata, and security analysis
        """
        # If no config provided, create from individual parameters or defaults
        if config is None:
            config = IngestConfig(
                mode=mode if mode is not None else "auto",
                strict=strict if strict is not None else True,
                model=model if model is not None else "gpt-4",
                timeout=timeout if timeout is not None else 30.0,
                stealth=stealth if stealth is not None else False,
                disable_http2=disable_http2 if disable_http2 is not None else False,
                extreme_mode=extreme_mode if extreme_mode is not None else False,
                screenshot=None if screenshot is _SCREENSHOT_UNSET else screenshot,
                extract_metadata=extract_metadata if extract_metadata is not None else True,
                extract_links=extract_links if extract_links is not None else True,
                advanced_security=advanced_security if advanced_security is not None else False,
                use_llm=use_llm if use_llm is not None else False,
            )
        else:
            config = config.clone()
            explicit_keys: set[str] = set(config.explicit_keys())
            # Config provided - override with any explicit parameters
            if mode is not None:
                config.mode = mode
                explicit_keys.add("mode")
            if strict is not None:
                config.strict = strict
                explicit_keys.add("strict")
            if model is not None:
                config.model = model
                explicit_keys.add("model")
            if timeout is not None:
                config.timeout = timeout
                explicit_keys.add("timeout")
            if stealth is not None:
                config.stealth = stealth
                explicit_keys.add("stealth")
            if disable_http2 is not None:
                config.disable_http2 = disable_http2
                explicit_keys.add("disable_http2")
            if extreme_mode is not None:
                config.extreme_mode = extreme_mode
                explicit_keys.add("extreme_mode")
            if screenshot is not _SCREENSHOT_UNSET:
                config.screenshot = screenshot
                explicit_keys.add("screenshot")
            if extract_metadata is not None:
                config.extract_metadata = extract_metadata
                explicit_keys.add("extract_metadata")
            if extract_links is not None:
                config.extract_links = extract_links
                explicit_keys.add("extract_links")
            if advanced_security is not None:
                config.advanced_security = advanced_security
                explicit_keys.add("advanced_security")
            if use_llm is not None:
                config.use_llm = use_llm
                explicit_keys.add("use_llm")
            object.__setattr__(config, "_explicit_keys", frozenset(explicit_keys))
            config.validate()

        from markdown_ingress.application.use_cases import IngestUseCase

        return IngestUseCase(
            orchestrator=self,
            playwright_available=PLAYWRIGHT_AVAILABLE,
            renderer_factory=lambda render_config: Renderer(config=render_config),
        ).execute(
            url=url,
            config=config,
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
        result = fn()
        duration_ms = (time.perf_counter() - started) * 1000.0
        record_stage_timing(stage, duration_ms)
        return result

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
