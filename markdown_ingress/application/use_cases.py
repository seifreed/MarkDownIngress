"""Application use cases coordinating infrastructure adapters and core pipeline logic."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from typing import Any, cast

from markdown_ingress.application.batch_processor import (
    _BatchContext,
    _BatchUrlProcessor,
    _CostBudget,
    _PreparedBatchRequest,
)
from markdown_ingress.application.bootstrap import (
    register_all_factories as _register_all_factories,
)
from markdown_ingress.application.fetcher_manager import (
    _ensure_fetcher_user_agent,
    _select_stable_fetcher_user_agent,
    _SharedFetcherManager,
)
from markdown_ingress.application.heuristics import (
    _looks_like_auth_interstitial,
    _looks_like_non_html_resource,
    _should_attempt_fast_degraded_fallback,
    _should_attempt_render_fallback,
)
from markdown_ingress.application.subprocess_runner import (
    _batch_process_context,
    _execute_batch_ingest_in_subprocess,
    _poll_subprocess_queue,
    _select_execution_strategy,
    _terminate_batch_process,
)
from markdown_ingress.config_models import DomainPolicy, IngestConfig, RenderConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.inflight import build_request_identity
from markdown_ingress.core.ingest_stats import (
    bump_ingest_stat,
    record_mode_request,
    record_mode_result,
    record_mode_timing,
    timed_stage_with_snapshot,
)
from markdown_ingress.core.interfaces import IFetcher, IIngestOrchestrator, IRenderer
from markdown_ingress.core.metadata_keys import (
    AUTO_MODE_REASON,
    AUTO_MODE_USED,
    CACHE_HIT,
    COST_UNITS_USED,
    DEGRADED_REASON,
    DEGRADED_RENDER_FALLBACK,
    EFFECTIVE_MODE,
    FAST_MODE_TOKENS,
    FETCH_METADATA,
    INFLIGHT_DEDUPLICATED,
    INFLIGHT_SHARED_COUNT,
    OPERATIONAL_FLAGS,
    RENDER_COST_BUDGET,
    REQUESTED_MODE,
    SCREENSHOT_TEMP,
)
from markdown_ingress.core.policy import (
    DomainCircuitOpenError,
    PolicyBlockedError,
    UnsupportedContentTypeError,
)
from markdown_ingress.core.ssrf import (
    resolve_allow_local_urls,
    validate_http_url_no_ssrf,
    validated_http_url_requires_dns_pinning,
)
from markdown_ingress.models import FetchResult, SafeDocument, SecurityReport
from markdown_ingress.reporting import security_report_from_document
from markdown_ingress.shared_results import BatchErrorItem, BatchResult

_logger = logging.getLogger(__name__)

# --- Bootstrap: register concrete adapters into core registries -----------------
_register_all_factories()
# -------------------------------------------------------------------------------

try:
    import playwright.async_api as _playwright_check  # noqa: F401

    PLAYWRIGHT_AVAILABLE: bool = True
    del _playwright_check
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

_AUTO_RENDER_MIN_IMPROVEMENT = 0.10
_RENDER_COST_BUDGET_CEILING: int = 5


class RenderUrlRequiresDnsPinningError(ValueError):
    """Raised when browser render cannot safely preserve DNS pinning semantics."""


def _cleanup_screenshot(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _purge_corrupt_cache_entry(cache_backend: Cache, cache_key: str) -> None:
    """Best-effort removal of a corrupt cache value before recomputing."""
    try:
        cache_backend.delete(cache_key)
    except Exception as exc:
        _logger.warning(
            "Failed to delete corrupt cache entry for %s; continuing as cache miss: %s",
            cache_key,
            exc,
            exc_info=True,
        )


class _CacheResolutionHelper:
    """Handles cache lookup and in-flight deduplication for ingestion requests."""

    def __init__(self, orchestrator: IIngestOrchestrator) -> None:
        self._orchestrator = orchestrator

    @staticmethod
    def make_cache_key(
        url: str,
        resolved_config: IngestConfig,
        request_identity: str,
        cache_backend: Cache | None,
    ) -> str | None:
        if cache_backend is None:
            return None
        return Cache.make_key(
            url=url,
            mode=resolved_config.mode,
            strict=resolved_config.strict,
            extra=request_identity,
        )

    def try_cache_hit(
        self,
        cache_backend: Cache | None,
        cache_key: str | None,
        requested_mode: str,
    ) -> SafeDocument | None:
        if cache_backend is None or cache_key is None:
            return None
        try:
            cached = cache_backend.get(cache_key)
        except Exception as exc:
            _logger.warning(
                "Cache lookup failed for %s; continuing without cache: %s",
                cache_key,
                exc,
                exc_info=True,
            )
            bump_ingest_stat("cache_misses")
            return None
        if cached is None:
            bump_ingest_stat("cache_misses")
            return None
        try:
            cached_copy = self._orchestrator.clone_cached_document(cached)
            bump_ingest_stat("cache_hits")
            cached_copy.metadata[REQUESTED_MODE] = requested_mode
            record_mode_result(requested_mode, success=True)
            return cached_copy
        except Exception as exc:
            _logger.warning(
                "Failed to clone cached document for %s, cache entry may be corrupt: %s",
                cache_key,
                exc,
                exc_info=True,
            )
            _purge_corrupt_cache_entry(cache_backend, cache_key)
            bump_ingest_stat("cache_misses")
            return None

    def try_inflight_follower(
        self,
        request_key: str,
        requested_mode: str,
    ) -> SafeDocument | None:
        in_flight = self._orchestrator.acquire_inflight(request_key)
        if in_flight is None:
            return None
        bump_ingest_stat("inflight_followers")
        shared = cast(SafeDocument, self._orchestrator.await_inflight(in_flight, request_key))
        shared.metadata[INFLIGHT_DEDUPLICATED] = True
        shared.metadata.setdefault(CACHE_HIT, False)
        shared.metadata[REQUESTED_MODE] = requested_mode
        record_mode_result(requested_mode, success=True)
        return shared

    def resolve(
        self,
        url: str,
        resolved_config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        cache_backend: Cache | None,
        request_key: str,
        requested_mode: str,
    ) -> tuple[SafeDocument | None, str | None]:
        """Return a cached or in-flight-shared document and the cache key, or (None, key)."""
        request_identity = build_request_identity(url, resolved_config, matched_domain_policy)
        cache_key = self.make_cache_key(url, resolved_config, request_identity, cache_backend)
        hit = self.try_cache_hit(cache_backend, cache_key, requested_mode)
        if hit is not None:
            return hit, cache_key
        shared = self.try_inflight_follower(request_key, requested_mode)
        return shared, cache_key


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
        _used_default_orchestrator = orchestrator is None
        if _used_default_orchestrator:
            from markdown_ingress.core.orchestrator import IngestOrchestrator

            orchestrator = IngestOrchestrator()
        self.orchestrator: IIngestOrchestrator = orchestrator
        self._used_default_orchestrator = _used_default_orchestrator
        self.fetcher_factory = fetcher_factory or self._default_fetcher_factory
        self.renderer_factory = renderer_factory or self._default_renderer_factory
        self.playwright_available = (
            PLAYWRIGHT_AVAILABLE if playwright_available is None else playwright_available
        )
        # Lambda defers to self.fetcher_factory so reassigning it after __init__ is honoured.
        self._fetcher_mgr = _SharedFetcherManager(lambda config: self.fetcher_factory(config))
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
            is orchestrator.inflight_registry
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

    @staticmethod
    def _cleanup_orphaned_screenshot(config: IngestConfig | RenderConfig) -> None:
        """Remove temporary screenshot file from a failed render attempt."""
        import os

        screenshot = config.screenshot
        if screenshot is True:
            return  # auto-temp path — renderer handles cleanup
        if isinstance(screenshot, str) and os.path.isfile(screenshot):
            try:
                os.unlink(screenshot)
            except OSError as exc:
                _logger.debug("Could not remove screenshot %s: %s", screenshot, exc)

    def execute(self, url: str, config: IngestConfig) -> SafeDocument:
        """Execute one ingestion request including cache/inflight handling and auto-mode fallback."""
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
            uses_temp_screenshot = resolved_config.screenshot is True
            cache_backend = None if uses_temp_screenshot else cast(Cache | None, config.cache)
            cache_key: str | None = None
            if not uses_temp_screenshot:
                request_key = self.orchestrator.make_request_key(
                    url, resolved_config, matched_domain_policy
                )
                early_return, cache_key = self._cache_resolver.resolve(
                    url,
                    resolved_config,
                    matched_domain_policy,
                    cache_backend,
                    request_key,
                    requested_mode,
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
                try:
                    self.orchestrator.release_inflight(request_key, error=exc)
                except KeyError:
                    pass
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
        budget = _CostBudget(limit=config.render_cost_budget)
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

        should_write_cache = config.screenshot is not True
        if should_write_cache and cache_backend is not None and cache_key is not None:
            try:
                cache_backend.set(cache_key, document, ttl=config.cache_ttl)
            except Exception as exc:
                _logger.warning(
                    "Cache write failed for %s; continuing without cache: %s",
                    cache_key,
                    exc,
                    exc_info=True,
                )
        document.metadata[CACHE_HIT] = False
        document.metadata[INFLIGHT_DEDUPLICATED] = False
        document.metadata[INFLIGHT_SHARED_COUNT] = 0
        return document


class _FetchPipeline:
    """Handles the fetch/render pipeline for a single URL ingestion request."""

    def __init__(
        self,
        orchestrator: IIngestOrchestrator,
        renderer_factory: Callable[[RenderConfig], IRenderer],
        get_shared_fetcher: Callable[[IngestConfig], IFetcher],
        playwright_available: bool,
    ) -> None:
        self._orchestrator = orchestrator
        self._renderer_factory = renderer_factory
        self._get_shared_fetcher = get_shared_fetcher
        self._playwright_available = playwright_available

    @staticmethod
    def _validate_render_url(url: str, config: IngestConfig) -> str:
        """Apply SSRF validation before handing the logical URL to Playwright."""
        validated_url = validate_http_url_no_ssrf(
            url,
            allow_local=resolve_allow_local_urls(config.allow_local_urls),
            resolve_dns=True,
        )
        if validated_http_url_requires_dns_pinning(url, validated_url):
            raise RenderUrlRequiresDnsPinningError(
                "Render URL requires DNS pinning that cannot be preserved safely"
            )
        return str(url).strip()

    def execute_mode(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        budget: _CostBudget,
    ) -> SafeDocument:
        fetch_result, operational_flags = self._fetch_result(url, config, budget)
        return self._orchestrator.process_fetched_content(
            fetch_result=fetch_result,
            config=config,
            matched_domain_policy=matched_domain_policy,
            operational_flags=operational_flags,
        )

    def _fetch_result(self, url: str, config: IngestConfig, budget: _CostBudget):
        operational_flags: list[str] = []
        stage_timings: dict[str, float] = {}

        def timed_stage(stage: str, fn: Callable[[], Any]) -> Any:
            return timed_stage_with_snapshot(stage_timings, stage, fn)

        if config.mode == "render":
            fetch_result = self._fetch_render(url, config, budget, timed_stage, operational_flags)
        else:
            fetch_result = self._fetch_fast(url, config, budget, timed_stage)

        fetch_result.metadata.setdefault(COST_UNITS_USED, budget.used)
        fetch_result.metadata.setdefault(RENDER_COST_BUDGET, budget.limit)
        fetch_result.metadata.setdefault("stage_timings_ms", stage_timings)
        return fetch_result, operational_flags

    @staticmethod
    def _prepare_render_config(config: IngestConfig) -> tuple[RenderConfig, str | None, bool]:
        """Build a RenderConfig, allocating a temp screenshot file when screenshot=True."""
        render_config = RenderConfig(
            timeout=config.timeout,
            wait_until="domcontentloaded",
            stealth=config.stealth,
            disable_http2=config.disable_http2,
            extreme_mode=config.extreme_mode,
            screenshot=config.screenshot,
            allow_local_urls=config.allow_local_urls,
        )
        screenshot_temp_path: str | None = None
        screenshot_was_temp = False
        if render_config.screenshot is True:
            tmp_file = tempfile.NamedTemporaryFile(
                suffix=".png",
                delete=False,
                prefix="mdingress_screenshot_",
            )
            screenshot_temp_path = tmp_file.name
            tmp_file.close()
            render_config.screenshot = screenshot_temp_path
            screenshot_was_temp = True
        return render_config, screenshot_temp_path, screenshot_was_temp

    def _execute_fast_degraded_fallback(
        self,
        url: str,
        config: IngestConfig,
        render_exc: Exception,
        budget: _CostBudget,
        timed_stage: Callable[[str, Callable[[], Any]], Any],
        operational_flags: list[str],
    ) -> FetchResult:
        budget.consume(1, "degraded fast fallback from render")
        fetcher = self._get_shared_fetcher(config)
        fetch_result = timed_stage("fetch_fast_degraded", lambda: fetcher.fetch_sync(url))
        operational_flags.extend(
            [
                "render_failed_fast_degraded_fallback",
                f"render_error:{type(render_exc).__name__}",
            ]
        )
        fetch_result.metadata[EFFECTIVE_MODE] = "fast"
        fetch_result.metadata[DEGRADED_RENDER_FALLBACK] = True
        fetch_result.metadata[DEGRADED_REASON] = str(render_exc)
        return fetch_result

    def _handle_render_failure(
        self,
        url: str,
        config: IngestConfig,
        render_exc: Exception,
        screenshot_temp_path: str | None,
        screenshot_was_temp: bool,
        budget: _CostBudget,
        timed_stage: Callable[[str, Callable[[], Any]], Any],
        operational_flags: list[str],
    ) -> FetchResult:
        if screenshot_was_temp and screenshot_temp_path is not None:
            try:
                os.unlink(screenshot_temp_path)
            except OSError as exc:
                _logger.debug("Could not remove screenshot %s: %s", screenshot_temp_path, exc)
        if not _should_attempt_fast_degraded_fallback(render_exc):
            raise render_exc
        return self._execute_fast_degraded_fallback(
            url, config, render_exc, budget, timed_stage, operational_flags
        )

    def _run_render_or_degrade(
        self,
        url: str,
        config: IngestConfig,
        render_config: RenderConfig,
        screenshot_temp_path: str | None,
        screenshot_was_temp: bool,
        budget: _CostBudget,
        timed_stage: Callable[[str, Callable[[], Any]], Any],
        operational_flags: list[str],
    ) -> FetchResult:
        """Run the Playwright render; fall back to a plain HTTP fetch on retryable failure."""
        renderer = self._renderer_factory(render_config)
        try:
            fetch_result = timed_stage("fetch_render", lambda: renderer.render_sync(url))
            if screenshot_was_temp:
                reported_screenshot_path = fetch_result.metadata.get("screenshot_path")
                if reported_screenshot_path:
                    if (
                        screenshot_temp_path is not None
                        and str(reported_screenshot_path) != screenshot_temp_path
                    ):
                        _cleanup_screenshot(screenshot_temp_path)
                    fetch_result.metadata[SCREENSHOT_TEMP] = True
                else:
                    _cleanup_screenshot(screenshot_temp_path)
            return fetch_result
        except Exception as render_exc:
            return self._handle_render_failure(
                url,
                config,
                render_exc,
                screenshot_temp_path,
                screenshot_was_temp,
                budget,
                timed_stage,
                operational_flags,
            )

    def _fetch_render(
        self,
        url: str,
        config: IngestConfig,
        budget: _CostBudget,
        timed_stage: Callable[[str, Callable[[], Any]], Any],
        operational_flags: list[str],
    ) -> FetchResult:
        if _looks_like_non_html_resource(url):
            raise UnsupportedContentTypeError(
                f"URL appears to target a non-HTML resource and should not be rendered: {url}"
            )
        render_url = self._validate_render_url(url, config)
        # The render cost budget is an upper bound on the combined
        # fast+render spend, so we only charge the delta up to 5 units.
        # A previous audit flagged this as undercharging, but the test
        # suite documents the opposite contract: fast probes already
        # consumed budget leave less room for render, by design.
        render_cost_units = max(0, _RENDER_COST_BUDGET_CEILING - budget.used)
        budget.consume(render_cost_units, "render mode")
        if not self._playwright_available:
            raise ImportError(
                "Render mode requires Playwright. Install with: "
                "pip install 'markdown-ingress[render]' && playwright install"
            )
        render_config, screenshot_temp_path, screenshot_was_temp = self._prepare_render_config(
            config
        )
        return self._run_render_or_degrade(
            render_url,
            config,
            render_config,
            screenshot_temp_path,
            screenshot_was_temp,
            budget,
            timed_stage,
            operational_flags,
        )

    def _fetch_fast(
        self,
        url: str,
        config: IngestConfig,
        budget: _CostBudget,
        timed_stage: Callable[[str, Callable[[], Any]], Any],
    ) -> FetchResult:
        budget.consume(1, "fetch mode")
        fetcher = self._get_shared_fetcher(config)
        try:
            return timed_stage("fetch_fast", lambda: fetcher.fetch_sync(url))
        except DomainCircuitOpenError:
            bump_ingest_stat("circuit_breaker_rejections")
            raise


class _AutoModeSelector:
    """Orchestrates auto-mode: fast probe, render evaluation, and winner selection."""

    def __init__(self, pipeline: _FetchPipeline, playwright_available: bool) -> None:
        self._pipeline = pipeline
        self._playwright_available = playwright_available

    def execute(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        budget: _CostBudget,
    ) -> SafeDocument:
        fast_config = config.clone()
        fast_config.mode = "fast"
        try:
            fast_doc = self._pipeline.execute_mode(url, fast_config, matched_domain_policy, budget)
        except Exception as exc:
            return self._auto_fallback_render_on_error(
                url, config, matched_domain_policy, budget, exc
            )
        if self._auto_skip_render(url, fast_doc, config):
            fast_doc.metadata[AUTO_MODE_USED] = "fast"
            return fast_doc
        return self._auto_render_with_fast_fallback(
            url, config, matched_domain_policy, budget, fast_doc
        )

    def _auto_fallback_render_on_error(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        budget: _CostBudget,
        exc: Exception,
    ) -> SafeDocument:
        """Try render mode after fast mode failed; re-raises if render is not applicable."""
        if (
            not self._playwright_available
            or _looks_like_non_html_resource(url)
            or _looks_like_auth_interstitial(url)
            or not _should_attempt_render_fallback(exc)
        ):
            raise exc
        render_config = config.clone()
        render_config.mode = "render"
        render_config.extreme_mode = True
        try:
            render_doc = self._pipeline.execute_mode(
                url, render_config, matched_domain_policy, budget
            )
        except RenderUrlRequiresDnsPinningError as render_exc:
            raise exc from render_exc
        render_doc.metadata[AUTO_MODE_USED] = "render"
        render_doc.metadata[AUTO_MODE_REASON] = "fast_failed"
        return render_doc

    def _auto_skip_render(self, url: str, fast_doc: SafeDocument, config: IngestConfig) -> bool:
        """Return True when render would be wasteful or unavailable."""
        return (
            fast_doc.token_estimate >= config.auto_render_threshold
            or not self._playwright_available
            or _looks_like_auth_interstitial(url)
            or _looks_like_non_html_resource(url)
        )

    def _auto_render_with_fast_fallback(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        budget: _CostBudget,
        fast_doc: SafeDocument,
    ) -> SafeDocument:
        """Attempt render and fall back to the already-fetched fast result on retryable failure."""
        render_config = config.clone()
        render_config.mode = "render"
        try:
            render_doc = self._pipeline.execute_mode(
                url, render_config, matched_domain_policy, budget
            )
        except RenderUrlRequiresDnsPinningError as exc:
            _logger.warning(
                "auto mode: render skipped because URL cannot be safely DNS-pinned. Error: %s",
                exc,
            )
            fast_doc.metadata[AUTO_MODE_USED] = "fast"
            fast_doc.metadata[AUTO_MODE_REASON] = "render_blocked_ssrf"
            return fast_doc
        except Exception as exc:
            if not _should_attempt_fast_degraded_fallback(exc):
                raise
            _logger.warning(
                "auto mode: render attempt failed, falling back to fast result. Error: %s", exc
            )
            fast_doc.metadata[AUTO_MODE_USED] = "fast"
            fast_doc.metadata[AUTO_MODE_REASON] = "render_fallback"
            return fast_doc
        return self._select_auto_mode_winner(fast_doc, render_doc)

    @staticmethod
    def _select_auto_mode_winner(fast_doc: SafeDocument, render_doc: SafeDocument) -> SafeDocument:
        """Pick the better result between fast and render docs based on token improvement."""
        render_fetch_metadata = render_doc.metadata.get(FETCH_METADATA, {})
        render_attempt_degraded = bool(render_fetch_metadata.get(DEGRADED_RENDER_FALLBACK))
        improvement_threshold = max(1, int(fast_doc.token_estimate * _AUTO_RENDER_MIN_IMPROVEMENT))
        if render_attempt_degraded:
            if render_doc.token_estimate >= fast_doc.token_estimate + improvement_threshold:
                render_doc.metadata[AUTO_MODE_USED] = "render"
                render_doc.metadata[AUTO_MODE_REASON] = "degraded_render"
                render_doc.metadata[FAST_MODE_TOKENS] = fast_doc.token_estimate
                return render_doc
            existing_flags = list(fast_doc.metadata.get(OPERATIONAL_FLAGS, []))
            for flag in render_doc.metadata.get(OPERATIONAL_FLAGS, []):
                if flag not in existing_flags:
                    existing_flags.append(flag)
            fast_doc.metadata[OPERATIONAL_FLAGS] = existing_flags
            fast_doc.metadata[AUTO_MODE_USED] = "fast"
            fast_doc.metadata[AUTO_MODE_REASON] = "render_fallback"
            return fast_doc
        if render_doc.token_estimate >= fast_doc.token_estimate + improvement_threshold:
            render_doc.metadata[AUTO_MODE_USED] = "render"
            render_doc.metadata[FAST_MODE_TOKENS] = fast_doc.token_estimate
            fast_fetch_metadata = fast_doc.metadata.get(FETCH_METADATA, {})
            if fast_fetch_metadata.get(SCREENSHOT_TEMP):
                _cleanup_screenshot(fast_fetch_metadata.get("screenshot_path"))
            return render_doc
        if render_fetch_metadata.get(SCREENSHOT_TEMP):
            _cleanup_screenshot(render_fetch_metadata.get("screenshot_path"))
        fast_doc.metadata[AUTO_MODE_USED] = "fast"
        return fast_doc


class BatchIngestUseCase:
    """Concurrent batch ingestion on top of the single-item ingestion use case."""

    def __init__(self, ingest_use_case: IngestUseCase | None = None) -> None:
        self.ingest_use_case = ingest_use_case or IngestUseCase()
        self._auto_fetcher_user_agent = getattr(
            self.ingest_use_case,
            "_auto_fetcher_user_agent",
            _select_stable_fetcher_user_agent(),
        )

    def _prepare_request(
        self,
        index: int,
        url: str,
        config: IngestConfig,
    ) -> _PreparedBatchRequest:
        resolved_config, matched_domain_policy = config.resolve_for_url(url)
        _ensure_fetcher_user_agent(
            url,
            resolved_config,
            matched_domain_policy,
            default_user_agent=self._auto_fetcher_user_agent,
        )
        cache_backend = (
            None if resolved_config.screenshot is True else cast(Cache | None, config.cache)
        )
        cache_key = None
        request_identity = build_request_identity(url, resolved_config, matched_domain_policy)
        request_key = self.ingest_use_case.orchestrator.make_request_key(
            url,
            resolved_config,
            matched_domain_policy,
        )
        if cache_backend is not None:
            cache_key = Cache.make_key(
                url=url,
                mode=resolved_config.mode,
                strict=resolved_config.strict,
                extra=request_identity,
            )
        return _PreparedBatchRequest(
            index=index,
            url=url,
            requested_mode=config.mode,
            resolved_config=resolved_config,
            request_key=request_key,
            cache_backend=cache_backend,
            cache_key=cache_key,
        )

    async def _execute_item_isolated(self, prepared: _PreparedBatchRequest) -> SafeDocument:
        ctx = _batch_process_context()
        if ctx is None:
            raise RuntimeError("Batch subprocess isolation requires an importable __main__ module")
        # Use Queue instead of Pipe: Queue handles arbitrarily large objects;
        # Pipe has a ~64 KB buffer that blocks the child on large SafeDocuments.
        queue = ctx.Queue()
        worker_config = prepared.resolved_config.clone()
        worker_config.cache = None
        process = ctx.Process(
            target=_execute_batch_ingest_in_subprocess,
            args=(
                prepared.url,
                worker_config,
                self.ingest_use_case.playwright_available,
                queue,
            ),
            daemon=True,
        )
        try:
            process.start()
            return await _poll_subprocess_queue(process, queue, prepared.url)
        except asyncio.CancelledError:
            _terminate_batch_process(process)
            raise
        finally:
            if process.is_alive():
                _terminate_batch_process(process)
            queue.close()
            queue.join_thread()

    async def _execute_item_in_process(self, prepared: _PreparedBatchRequest) -> SafeDocument:
        """Execute a batch item locally while preserving injected dependencies."""
        worker_config = prepared.resolved_config.clone()
        worker_config.cache = None
        return await asyncio.to_thread(
            self.ingest_use_case.execute,
            prepared.url,
            worker_config,
        )

    async def execute(
        self,
        urls: Sequence[str],
        config_builder: Callable[[], IngestConfig],
        *,
        max_concurrent: int = 5,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> BatchResult:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")

        url_list = list(urls)
        total = len(url_list)
        documents: list[SafeDocument | None] = [None] * total
        errors: list[BatchErrorItem] = []
        execution_strategy, fallback_reason = _select_execution_strategy(self.ingest_use_case)
        if execution_strategy == "local" and fallback_reason is not None:
            _logger.info("Batch falling back to in-process execution: %s.", fallback_reason)
        prepared_requests = [
            self._prepare_request(index, url, config_builder())
            for index, url in enumerate(url_list)
        ]
        ctx = _BatchContext(
            total=total,
            documents=documents,
            errors=errors,
            semaphore=asyncio.Semaphore(max_concurrent),
            errors_lock=asyncio.Lock(),
            batch_inflight={},
            batch_inflight_lock=asyncio.Lock(),
            progress_lock=asyncio.Lock(),
            completed=0,
            execution_strategy=execution_strategy,
            on_progress=on_progress,
            batch_tracks_metrics=execution_strategy != "local",
        )
        processor = _BatchUrlProcessor(ctx, self)
        tasks = [asyncio.create_task(processor.process(prepared)) for prepared in prepared_requests]
        try:
            results = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        successful = sum(1 for r in results if r)
        failed = total - successful
        return BatchResult(
            total=total,
            successful=successful,
            failed=failed,
            documents=documents,
            errors=errors,
        )


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
