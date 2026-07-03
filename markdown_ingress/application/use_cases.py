"""Application use cases coordinating infrastructure adapters and core pipeline logic."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import suppress
from typing import cast

from markdown_ingress.application.batch_ingest_use_case import (
    BatchIngestUseCase as BatchIngestUseCase,
)
from markdown_ingress.application.batch_state import CostBudget
from markdown_ingress.application.bootstrap import (
    register_all_factories as _register_all_factories,
)
from markdown_ingress.application.cache_resolution import (
    CacheResolutionRequest,
    _CacheResolutionHelper,
    write_cache_entry,
)
from markdown_ingress.application.cache_resolution import (
    _purge_corrupt_cache_entry as _purge_corrupt_cache_entry_impl,
)
from markdown_ingress.application.fetch_pipeline import (
    _AutoModeSelector,
    _FetchPipeline,
)
from markdown_ingress.application.fetcher_manager import (
    _ensure_fetcher_user_agent,
    _select_stable_fetcher_user_agent,
    _SharedFetcherManager,
)
from markdown_ingress.application.heuristics import (
    _looks_like_auth_interstitial as _looks_like_auth_interstitial,
)
from markdown_ingress.application.screenshot_policy import screenshot_requires_fresh_capture
from markdown_ingress.config_models import DomainPolicy, IngestConfig, RenderConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.ingest_stats import (
    bump_ingest_stat,
    record_mode_request,
    record_mode_result,
    record_mode_timing,
)
from markdown_ingress.core.interfaces import IFetcher, IIngestOrchestrator, IRenderer
from markdown_ingress.core.metadata_keys import (
    CACHE_HIT,
    INFLIGHT_DEDUPLICATED,
    INFLIGHT_SHARED_COUNT,
    REQUESTED_MODE,
)
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.reporting import security_report_from_document
from markdown_ingress.runtime_helpers import is_dependency_available

_logger = logging.getLogger(__name__)
_FACTORIES_REGISTERED = False

PLAYWRIGHT_AVAILABLE = is_dependency_available("playwright")


def _purge_corrupt_cache_entry(cache_backend: Cache, cache_key: str) -> None:
    """Compatibility wrapper for the moved cache purge helper."""
    return _purge_corrupt_cache_entry_impl(cache_backend, cache_key)


def _ensure_factories_registered() -> None:
    global _FACTORIES_REGISTERED
    if not _FACTORIES_REGISTERED:
        _register_all_factories()
        _FACTORIES_REGISTERED = True


def _select_orchestrator(
    orchestrator: IIngestOrchestrator | None,
) -> tuple[IIngestOrchestrator, bool]:
    if orchestrator is not None:
        return orchestrator, False

    from markdown_ingress.core.orchestrator import IngestOrchestrator

    return cast(IIngestOrchestrator, IngestOrchestrator()), True


class IngestUseCase:
    """Coordinate runtime policy, infrastructure selection, cache, and inflight dedup."""

    def __init__(
        self,
        orchestrator: IIngestOrchestrator | None = None,
        fetcher_factory: Callable[[IngestConfig], IFetcher] | None = None,
        renderer_factory: Callable[[RenderConfig], IRenderer] | None = None,
        *,
        playwright_available: bool | None = None,
    ) -> None:
        _ensure_factories_registered()
        selected_orchestrator, _used_default_orchestrator = _select_orchestrator(orchestrator)
        self.orchestrator: IIngestOrchestrator = selected_orchestrator
        self._used_default_orchestrator = _used_default_orchestrator
        self.fetcher_factory = fetcher_factory or self._default_fetcher_factory
        self.renderer_factory = renderer_factory or self._default_renderer_factory
        self.playwright_available = (
            PLAYWRIGHT_AVAILABLE if playwright_available is None else playwright_available
        )

        def current_fetcher_factory(config: IngestConfig) -> IFetcher:
            return self.fetcher_factory(config)

        self._fetcher_mgr = _SharedFetcherManager(current_fetcher_factory)
        self._auto_fetcher_user_agent = _select_stable_fetcher_user_agent()
        self._cache_resolver = _CacheResolutionHelper(self.orchestrator)

    @staticmethod
    def _default_fetcher_factory(config: IngestConfig) -> IFetcher:
        from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

        return Fetcher(
            timeout=config.timeout,
            user_agent=getattr(config, "fetcher_user_agent", None),
            allow_local_urls=config.allow_local_urls,
            domain_request_interval=config.domain_request_interval,
            circuit_breaker_threshold=config.circuit_breaker_threshold,
            circuit_breaker_open_seconds=config.circuit_breaker_open_seconds,
        )

    @staticmethod
    def _default_renderer_factory(config: RenderConfig) -> IRenderer:
        from markdown_ingress.adapters.rendering.playwright_renderer import PlaywrightRenderer

        return PlaywrightRenderer(config=config)

    def close(self) -> None:
        """Close shared resources (fetcher, etc.)."""
        self._fetcher_mgr.close()

    @staticmethod
    def _matches_default_factory(
        factory: object,
        owner: object,
        method_name: str,
    ) -> bool:
        """Return whether a stored factory still points at the default bound method."""
        default_factory = getattr(type(owner), method_name)
        return factory is default_factory or (
            getattr(factory, "__self__", None) is owner
            and getattr(factory, "__func__", None) is default_factory
        )

    def uses_default_runtime_dependencies(self) -> bool:
        """Return whether batch subprocess execution would preserve runtime semantics."""
        uses_default_fetcher = self._matches_default_factory(
            self.fetcher_factory,
            self,
            "_default_fetcher_factory",
        )
        uses_default_renderer = self._matches_default_factory(
            self.renderer_factory,
            self,
            "_default_renderer_factory",
        )
        orchestrator = self.orchestrator
        uses_default_orchestrator = (
            self._used_default_orchestrator
            and not getattr(orchestrator, "_inflight_registry_was_injected", False)
            and getattr(orchestrator, "_default_inflight_registry", None)
            is getattr(orchestrator, "inflight_registry", None)
            and all(
                getattr(orchestrator, name) is None
                for name in (
                    "extractor",
                    "normalizer",
                    "md_converter",
                    "hasher",
                    "token_estimator",
                    "scorer",
                    "metadata_extractor",
                    "link_analyzer",
                )
            )
        )
        return uses_default_fetcher and uses_default_renderer and uses_default_orchestrator

    def execute(self, url: str, config: IngestConfig) -> SafeDocument:
        """Execute one ingestion request with cache, inflight handling, and auto fallback."""
        bump_ingest_stat("requests_total")
        started_at = time.perf_counter()
        requested_mode = config.mode
        record_mode_request(requested_mode)
        request_key: str | None = None
        leader_slot_acquired = False
        resolved_config, matched_domain_policy = config.resolve_for_url(url)
        _ensure_fetcher_user_agent(
            url,
            resolved_config,
            matched_domain_policy,
            default_user_agent=self._auto_fetcher_user_agent,
        )
        try:
            fresh_screenshot = screenshot_requires_fresh_capture(resolved_config)
            cache_backend = None if fresh_screenshot else cast(Cache | None, config.cache)
            cache_key: str | None = None
            if not fresh_screenshot:
                request_key = self.orchestrator.make_request_key(
                    url, resolved_config, matched_domain_policy
                )
                early_return, cache_key = self._cache_resolver.resolve(
                    CacheResolutionRequest(
                        url=url,
                        resolved_config=resolved_config,
                        matched_domain_policy=matched_domain_policy,
                        cache_backend=cache_backend,
                        request_key=request_key,
                        requested_mode=requested_mode,
                    )
                )
                if early_return is not None:
                    return early_return
                leader_slot_acquired = True
            bump_ingest_stat("leader_executions")
            document = self._execute_uncached(
                url, resolved_config, matched_domain_policy, cache_backend, cache_key
            )
        except Exception as exc:
            if request_key is not None and leader_slot_acquired:
                with suppress(KeyError):
                    self.orchestrator.release_inflight(request_key, error=exc)
            record_mode_result(requested_mode, success=False)
            raise
        finally:
            record_mode_timing(requested_mode, (time.perf_counter() - started_at) * 1000.0)

        document.metadata[REQUESTED_MODE] = requested_mode
        shared_count = 0
        if request_key is not None and leader_slot_acquired:
            shared_count = self.orchestrator.release_inflight(request_key, document=document)
        document.metadata[INFLIGHT_SHARED_COUNT] = shared_count
        record_mode_result(requested_mode, success=True)
        return document

    def _execute_uncached(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        cache_backend: Cache | None,
        cache_key: str | None,
    ) -> SafeDocument:
        budget = CostBudget(limit=config.render_cost_budget)
        pipeline = _FetchPipeline(
            orchestrator=self.orchestrator,
            renderer_factory=self.renderer_factory,
            get_shared_fetcher=self._fetcher_mgr.get,
            playwright_available=self.playwright_available,
        )
        if config.mode == "auto":
            document = _AutoModeSelector(pipeline, self.playwright_available).execute(
                url, config, matched_domain_policy, budget
            )
        else:
            document = pipeline.execute_mode(url, config, matched_domain_policy, budget)

        if not screenshot_requires_fresh_capture(config):
            write_cache_entry(cache_backend, cache_key, document, ttl=config.cache_ttl)
        document.metadata[CACHE_HIT] = False
        document.metadata[INFLIGHT_DEDUPLICATED] = False
        document.metadata[INFLIGHT_SHARED_COUNT] = 0
        return document


class GenerateSecurityReportUseCase:
    """Build a security report from a SafeDocument-oriented ingestion result."""

    def __init__(self, ingest_use_case: IngestUseCase | None = None) -> None:
        self.ingest_use_case = ingest_use_case or IngestUseCase()

    def execute(self, url: str, config: IngestConfig) -> SecurityReport:
        try:
            doc = self.ingest_use_case.execute(url, config)
        except PolicyBlockedError as exc:
            if exc.document is None:
                raise
            doc = exc.document
        return security_report_from_document(doc)


class CompareExtractorsUseCase:
    """Evaluate alternative extractors behind an application-level boundary."""

    def execute(self, html: str, *, model: str = "gpt-4") -> dict[str, dict[str, object]]:
        from markdown_ingress.adapters.extractors.comparison import compare_extractors

        return compare_extractors(html, model=model)
