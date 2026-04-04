"""Application use cases coordinating infrastructure adapters and core pipeline logic."""

from __future__ import annotations

import asyncio
import copy
import logging
import multiprocessing
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit
from typing import Any, Literal, cast

import httpx

from markdown_ingress.adapters.extractors.comparison import compare_extractors
from markdown_ingress.adapters.rendering.playwright_renderer import (
    PLAYWRIGHT_AVAILABLE,
    PlaywrightRenderer,
)
from markdown_ingress.config_models import IngestConfig, RenderConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.fetcher import (
    DomainCircuitOpenError,
    Fetcher,
    UnsupportedContentTypeError,
)
from markdown_ingress.core.inflight import build_request_identity
from markdown_ingress.core.ingest_stats import (
    bump_ingest_stat,
    record_mode_request,
    record_mode_result,
    record_mode_timing,
    record_stage_timing,
)
from markdown_ingress.core.interfaces import IFetcher, IRenderer
from markdown_ingress.core.orchestrator import IngestOrchestrator
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.models import SecurityReport, SafeDocument
from markdown_ingress.shared_results import BatchErrorItem, BatchResult

_logger = logging.getLogger(__name__)

_NON_HTML_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bin",
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".exe",
    ".gz",
    ".iso",
    ".jpeg",
    ".jpg",
    ".json",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rss",
    ".svg",
    ".tar",
    ".tgz",
    ".txt",
    ".wav",
    ".webm",
    ".xml",
    ".xls",
    ".xlsx",
    ".zip",
}
_AUTH_PATH_TOKENS = (
    "account",
    "accounts",
    "auth",
    "login",
    "oauth",
    "signin",
    "sign-in",
    "signup",
    "sign-up",
)


def _looks_like_non_html_resource(url: str) -> bool:
    """Best-effort URL heuristic to avoid launching Playwright for obvious downloads."""
    path = urlsplit(url).path.lower()
    return any(path.endswith(extension) for extension in _NON_HTML_EXTENSIONS)


def _looks_like_auth_interstitial(url: str) -> bool:
    """Skip costly auto-render for account/login flows that rarely improve via Playwright."""
    parsed = urlsplit(url)
    tokens = set()
    for part in (
        parsed.hostname or "",
        parsed.path,
        parsed.query,
    ):
        if not part:
            continue
        tokens.update(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", part.lower()))
    return any(token in tokens for token in _AUTH_PATH_TOKENS)


def _should_attempt_render_fallback(exc: Exception) -> bool:
    """Limit auto-mode render fallback to failures a browser may realistically improve."""
    if isinstance(exc, (DomainCircuitOpenError, UnsupportedContentTypeError)):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {403, 429, 503}
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL, httpx.ConnectError)):
        return False
    if isinstance(exc, httpx.TimeoutException):
        return False
    message = str(exc).lower()
    if "name or service not known" in message or "nodename nor servname" in message:
        return False
    if "request url has an unsupported protocol" in message:
        return False
    if "unsupported content type" in message:
        return False
    return True


@dataclass
class _PreparedBatchRequest:
    index: int
    url: str
    requested_mode: str
    resolved_config: IngestConfig
    request_key: str
    cache_backend: Cache | None
    cache_key: str | None


@dataclass
class _BatchInFlightRecord:
    future: asyncio.Future[tuple[SafeDocument, int]]
    followers: int = 0


def _copy_batch_exception(exc: Exception) -> Exception:
    try:
        return copy.deepcopy(exc)
    except Exception:
        try:
            return type(exc)(str(exc))
        except Exception:
            return RuntimeError(f"{type(exc).__name__}: {exc}")


def _execute_batch_ingest_in_subprocess(
    url: str,
    config: IngestConfig,
    playwright_available: bool,
    conn,
) -> None:
    try:
        document = IngestUseCase(playwright_available=playwright_available).execute(url, config)
        conn.send(("result", document))
    except BaseException as exc:  # pragma: no cover - child process path
        try:
            conn.send(("exception", exc))
        except Exception:
            conn.send(("exception_payload", {"type": type(exc).__name__, "message": str(exc)}))
    finally:
        conn.close()


def _should_attempt_fast_degraded_fallback(exc: Exception) -> bool:
    """Allow render mode to degrade to a plain HTTP fetch for transient browser/runtime failures."""
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL, UnsupportedContentTypeError)):
        return False
    message = str(exc).lower()
    retryable_tokens = (
        "err_failed",
        "err_internet_disconnected",
        "err_network_io_suspended",
        "timeout",
        "page is navigating",
        "page.content",
    )
    return any(token in message for token in retryable_tokens)


class IngestUseCase:
    """Coordinate runtime policy, infrastructure selection, cache, and inflight dedup."""

    def __init__(
        self,
        orchestrator: IngestOrchestrator | None = None,
        fetcher_factory: Callable[[IngestConfig], IFetcher] | None = None,
        renderer_factory: Callable[[RenderConfig], IRenderer] | None = None,
        *,
        playwright_available: bool | None = None,
    ) -> None:
        self.orchestrator = orchestrator or IngestOrchestrator()
        self.fetcher_factory = fetcher_factory or self._default_fetcher_factory
        self.renderer_factory = renderer_factory or self._default_renderer_factory
        self.playwright_available = (
            PLAYWRIGHT_AVAILABLE if playwright_available is None else playwright_available
        )

    @staticmethod
    def _default_fetcher_factory(config: IngestConfig) -> IFetcher:
        return Fetcher(
            timeout=config.timeout,
            allow_local_urls=config.allow_local_urls,
            domain_request_interval=config.domain_request_interval,
            circuit_breaker_threshold=config.circuit_breaker_threshold,
            circuit_breaker_open_seconds=config.circuit_breaker_open_seconds,
        )

    @staticmethod
    def _default_renderer_factory(config: RenderConfig) -> IRenderer:
        return PlaywrightRenderer(config=config)

    @staticmethod
    def _matches_default_factory(
        factory: object,
        owner: object,
        method_name: str,
    ) -> bool:
        """Return whether a stored factory still points at the default bound method."""
        default_factory = getattr(type(owner), method_name)
        return (
            factory is default_factory
            or (
                getattr(factory, "__self__", None) is owner
                and getattr(factory, "__func__", None) is default_factory
            )
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
            type(orchestrator) is IngestOrchestrator
            and not getattr(orchestrator, "_inflight_registry_was_injected", True)
            and getattr(orchestrator, "_default_inflight_registry", None) is orchestrator.inflight_registry
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
    def _cleanup_orphaned_screenshot(config: IngestConfig) -> None:
        """Remove temporary screenshot file from a failed render attempt."""
        import os
        screenshot = config.screenshot
        if screenshot is True:
            return  # auto-temp path — renderer handles cleanup
        if isinstance(screenshot, str) and os.path.isfile(screenshot):
            try:
                os.unlink(screenshot)
            except OSError:
                pass

    def execute(self, url: str, config: IngestConfig) -> SafeDocument:
        """Execute one ingestion request including cache/inflight handling and auto-mode fallback."""
        bump_ingest_stat("requests_total")
        started_at = time.perf_counter()
        requested_mode = config.mode
        record_mode_request(requested_mode)
        request_key: str | None = None
        leader_slot_acquired = False
        resolved_config, matched_domain_policy = config.resolve_for_url(url)
        try:
            cache_backend = cast(Cache | None, config.cache)
            cache_key = None
            request_identity = build_request_identity(url, resolved_config, matched_domain_policy)
            request_key = self.orchestrator.make_request_key(
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
                cached = cache_backend.get(cache_key)
                if cached is not None:
                    bump_ingest_stat("cache_hits")
                    try:
                        cached_copy = self.orchestrator.clone_cached_document(cached)
                        cached_copy.metadata["requested_mode"] = requested_mode
                        record_mode_result(requested_mode, success=True)
                        return cached_copy
                    except Exception:
                        record_mode_result(requested_mode, success=False)
                        raise
                bump_ingest_stat("cache_misses")

            # Set flag BEFORE acquire_inflight to ensure slot is released on any exception.
            # If we become a follower (in_flight is not None), we'll reset it below.
            # This prevents slot leaks if an interruption occurs between acquire_inflight
            # returning None and setting the flag.
            leader_slot_acquired = True
            in_flight = self.orchestrator.acquire_inflight(request_key)
            if in_flight is not None:
                # We're a follower, not a leader - reset the flag
                leader_slot_acquired = False
                bump_ingest_stat("inflight_followers")
                shared = self.orchestrator.await_inflight(in_flight, request_key)
                shared.metadata["inflight_deduplicated"] = True
                shared.metadata.setdefault("cache_hit", False)
                shared.metadata["requested_mode"] = requested_mode
                record_mode_result(requested_mode, success=True)
                return shared
            bump_ingest_stat("leader_executions")
            document = self._execute_uncached(
                url,
                resolved_config,
                matched_domain_policy,
                cache_backend,
                cache_key,
            )
        except Exception as exc:
            # Release in-flight slot only if we acquired it (leader path)
            if request_key is not None and leader_slot_acquired:
                try:
                    self.orchestrator.release_inflight(request_key, error=exc)
                except KeyError:
                    pass
            record_mode_result(requested_mode, success=False)
            raise
        finally:
            record_mode_timing(requested_mode, (time.perf_counter() - started_at) * 1000.0)

        document.metadata["requested_mode"] = requested_mode
        shared_count = self.orchestrator.release_inflight(request_key, document=document)
        document.metadata["inflight_shared_count"] = shared_count
        # Note: record_mode_result already called for cache hits (line 219) and inflight followers (line 229)
        # Leader success is recorded here
        record_mode_result(requested_mode, success=True)
        return document

    def _execute_uncached(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy,
        cache_backend: Cache | None,
        cache_key: str | None,
    ) -> SafeDocument:
        budget_state = {
            "budget": config.render_cost_budget,
            "used": 0,
        }
        if config.mode == "auto":
            document = self._execute_auto(url, config, matched_domain_policy, budget_state)
        else:
            document = self._execute_explicit_mode(url, config, matched_domain_policy, budget_state)

        if cache_backend is not None and cache_key is not None:
            cache_backend.set(cache_key, document, ttl=config.cache_ttl)
        document.metadata["cache_hit"] = False
        document.metadata["inflight_deduplicated"] = False
        document.metadata["inflight_shared_count"] = 0
        return document

    def _execute_auto(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy,
        budget_state: dict[str, int | None],
    ) -> SafeDocument:
        fast_config = config.clone()
        fast_config.mode = "fast"

        try:
            fast_doc = self._execute_explicit_mode(url, fast_config, matched_domain_policy, budget_state)
        except Exception as exc:
            if (
                not self.playwright_available
                or _looks_like_non_html_resource(url)
                or _looks_like_auth_interstitial(url)
                or not _should_attempt_render_fallback(exc)
            ):
                raise exc
            render_config = config.clone()
            render_config.mode = "render"
            render_config.extreme_mode = True
            render_doc = self._execute_explicit_mode(url, render_config, matched_domain_policy, budget_state)
            render_doc.metadata["auto_mode_used"] = "render"
            render_doc.metadata["auto_mode_reason"] = "fast_failed"
            return render_doc

        if (
            fast_doc.token_estimate >= config.auto_render_threshold
            or not self.playwright_available
            or _looks_like_auth_interstitial(url)
        ):
            fast_doc.metadata["auto_mode_used"] = "fast"
            return fast_doc

        render_config = config.clone()
        render_config.mode = "render"
        try:
            render_doc = self._execute_explicit_mode(url, render_config, matched_domain_policy, budget_state)
        except Exception as exc:
            self._cleanup_orphaned_screenshot(render_config)
            if not _should_attempt_fast_degraded_fallback(exc):
                raise
            _logger.warning("auto mode: render attempt failed, falling back to fast result. Error: %s", exc)
            fast_doc.metadata["auto_mode_used"] = "fast"
            fast_doc.metadata["auto_mode_reason"] = "render_fallback"
            return fast_doc
        if render_doc.token_estimate > fast_doc.token_estimate:
            render_doc.metadata["auto_mode_used"] = "render"
            render_doc.metadata["fast_mode_tokens"] = fast_doc.token_estimate
            return render_doc

        fast_doc.metadata["auto_mode_used"] = "fast"
        return fast_doc

    def _execute_explicit_mode(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy,
        budget_state: dict[str, int | None],
    ) -> SafeDocument:
        fetch_result, operational_flags = self._fetch_result(url, config, budget_state)
        return self.orchestrator.process_fetched_content(
            fetch_result=fetch_result,
            config=config,
            matched_domain_policy=matched_domain_policy,
            operational_flags=operational_flags,
        )

    def _fetch_result(self, url: str, config: IngestConfig, budget_state: dict[str, int | None]):
        cost_budget = budget_state["budget"]
        operational_flags: list[str] = []
        stage_timings: dict[str, float] = {}

        def timed_stage(stage: str, fn):
            started = time.perf_counter()
            result = fn()
            duration_ms = (time.perf_counter() - started) * 1000.0
            stage_timings[stage] = duration_ms
            record_stage_timing(stage, duration_ms)
            return result

        def consume_cost(units: int, reason: str) -> None:
            used = int(budget_state["used"] or 0)
            if cost_budget is None:
                budget_state["used"] = used + units
                return
            if used + units > cost_budget:
                raise RuntimeError(
                    f"Render cost budget exceeded while handling {reason}: "
                    f"required {used + units}, budget {cost_budget}"
                )
            budget_state["used"] = used + units

        if config.mode == "render":
            if _looks_like_non_html_resource(url):
                raise UnsupportedContentTypeError(
                    f"URL appears to target a non-HTML resource and should not be rendered: {url}"
                )
            consume_cost(5, "render mode")
            if not self.playwright_available:
                raise ImportError(
                    "Render mode requires Playwright. Install with: "
                    "pip install 'markdown-ingress[render]' && playwright install"
                )
            render_config = RenderConfig(
                timeout=config.timeout,
                wait_until="domcontentloaded",
                stealth=config.stealth,
                disable_http2=config.disable_http2,
                extreme_mode=config.extreme_mode,
                screenshot=config.screenshot,
            )
            renderer = self.renderer_factory(render_config)
            try:
                fetch_result = timed_stage(
                    "fetch_render",
                    lambda: renderer.render_sync(url),
                )
            except Exception as render_exc:
                if not _should_attempt_fast_degraded_fallback(render_exc):
                    # Clean up any orphaned screenshot from the failed render attempt
                    self._cleanup_orphaned_screenshot(config)
                    raise
                # Clean up any orphaned screenshot from the failed render attempt
                self._cleanup_orphaned_screenshot(config)
                consume_cost(1, "degraded fast fallback from render")
                degraded_fetcher = self.fetcher_factory(config)
                fetch_result = timed_stage(
                    "fetch_fast_degraded",
                    lambda: degraded_fetcher.fetch_sync(url),
                )
                operational_flags.extend(
                    [
                        "render_failed_fast_degraded_fallback",
                        f"render_error:{type(render_exc).__name__}",
                    ]
                )
                fetch_result.metadata["effective_mode"] = "fast"
                fetch_result.metadata["degraded_render_fallback"] = True
                fetch_result.metadata["degraded_reason"] = str(render_exc)
        else:
            consume_cost(1, "fetch mode")
            fetcher = self.fetcher_factory(config)
            try:
                fetch_result = timed_stage(
                    "fetch_fast",
                    lambda: fetcher.fetch_sync(url),
                )
            except DomainCircuitOpenError:
                bump_ingest_stat("circuit_breaker_rejections")
                raise
            except Exception as fast_exc:
                if (
                    not self.playwright_available
                    or config.mode != "auto"
                    or not _should_attempt_render_fallback(fast_exc)
                ):
                    raise
                bump_ingest_stat("render_fallbacks")
                consume_cost(5, "auto render fallback")
                render_config = RenderConfig(
                    timeout=config.timeout,
                    wait_until="domcontentloaded",
                    stealth=config.stealth,
                    disable_http2=config.disable_http2,
                    extreme_mode=True,
                    screenshot=config.screenshot,
                )
                renderer = self.renderer_factory(render_config)
                operational_flags.append("fast_fetch_failed_render_fallback")
                fetch_result = timed_stage(
                    "fetch_render_fallback",
                    lambda: renderer.render_sync(url),
                )
        fetch_result.metadata.setdefault("cost_units_used", int(budget_state["used"] or 0))
        fetch_result.metadata.setdefault("render_cost_budget", cost_budget)
        fetch_result.metadata.setdefault("stage_timings_ms", stage_timings)
        return fetch_result, operational_flags


class BatchIngestUseCase:
    """Concurrent batch ingestion on top of the single-item ingestion use case."""

    def __init__(self, ingest_use_case: IngestUseCase | None = None) -> None:
        self.ingest_use_case = ingest_use_case or IngestUseCase()

    @staticmethod
    def _main_module_file() -> str | None:
        """Return the current main-module path when subprocess respawn is safe."""
        main_module = sys.modules.get("__main__")
        path = getattr(main_module, "__file__", None)
        if not isinstance(path, str) or not path or path.startswith("<"):
            return None
        if not os.path.isfile(path):
            return None
        return path

    def _prepare_request(
        self,
        index: int,
        url: str,
        config: IngestConfig,
    ) -> _PreparedBatchRequest:
        resolved_config, matched_domain_policy = config.resolve_for_url(url)
        cache_backend = cast(Cache | None, config.cache)
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

    @staticmethod
    def _batch_process_context():
        if BatchIngestUseCase._main_module_file() is None:
            return None
        available = multiprocessing.get_all_start_methods()
        for method in ("forkserver", "spawn", "fork"):
            if method in available:
                return multiprocessing.get_context(method)
        return multiprocessing.get_context()

    def _select_execution_strategy(self) -> tuple[Literal["isolated", "local"], str | None]:
        """Choose between subprocess isolation and in-process execution."""
        if not self.ingest_use_case.uses_default_runtime_dependencies():
            return "local", "custom runtime dependencies"
        if self._batch_process_context() is None:
            return "local", "main module is not importable from a file"
        return "isolated", None

    @staticmethod
    def _terminate_batch_process(process) -> None:
        if not process.is_alive():
            process.join(timeout=0.5)
            return
        process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)

    async def _execute_item_isolated(self, prepared: _PreparedBatchRequest) -> SafeDocument:
        ctx = self._batch_process_context()
        if ctx is None:
            raise RuntimeError("Batch subprocess isolation requires an importable __main__ module")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        worker_config = prepared.resolved_config.clone()
        worker_config.cache = None
        process = ctx.Process(
            target=_execute_batch_ingest_in_subprocess,
            args=(
                prepared.url,
                worker_config,
                self.ingest_use_case.playwright_available,
                child_conn,
            ),
            daemon=True,
        )
        try:
            process.start()
            child_conn.close()
            while True:
                if parent_conn.poll(0.0):
                    try:
                        kind, payload = parent_conn.recv()
                    except EOFError as exc:
                        raise RuntimeError(
                            f"Batch worker exited before returning a result for {prepared.url}"
                        ) from exc
                    process.join(timeout=0.5)
                    if kind == "result":
                        return payload
                    if kind == "exception":
                        raise payload
                    if kind == "exception_payload":
                        raise RuntimeError(f"{payload['type']}: {payload['message']}")
                    raise RuntimeError(
                        f"Batch worker returned an unknown payload for {prepared.url}"
                    )
                if not process.is_alive():
                    process.join(timeout=0.5)
                    raise RuntimeError(
                        f"Batch worker exited without returning a result for {prepared.url}"
                    )
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self._terminate_batch_process(process)
            raise
        finally:
            parent_conn.close()
            if process.is_alive():
                self._terminate_batch_process(process)

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
        semaphore = asyncio.Semaphore(max_concurrent)
        errors_lock = asyncio.Lock()  # Protect concurrent errors.append() calls
        batch_inflight: dict[str, _BatchInFlightRecord] = {}
        batch_inflight_lock = asyncio.Lock()
        execution_strategy, fallback_reason = self._select_execution_strategy()
        if execution_strategy == "local" and fallback_reason is not None:
            _logger.info(
                "Batch falling back to in-process execution: %s.",
                fallback_reason,
            )
        prepared_requests = [
            self._prepare_request(index, url, config_builder())
            for index, url in enumerate(url_list)
        ]

        async def process_url(prepared: _PreparedBatchRequest) -> bool:
            """Return True on success, False on failure."""
            started_at = time.perf_counter()
            bump_ingest_stat("requests_total")
            record_mode_request(prepared.requested_mode)
            async with semaphore:
                if on_progress is not None:
                    on_progress(prepared.index + 1, total, prepared.url)
                try:
                    if prepared.cache_backend is not None and prepared.cache_key is not None:
                        cached = prepared.cache_backend.get(prepared.cache_key)
                        if cached is not None:
                            bump_ingest_stat("cache_hits")
                            cached_copy = self.ingest_use_case.orchestrator.clone_cached_document(cached)
                            cached_copy.metadata["requested_mode"] = prepared.requested_mode
                            documents[prepared.index] = cached_copy
                            record_mode_result(prepared.requested_mode, success=True)
                            return True
                        bump_ingest_stat("cache_misses")

                    async with batch_inflight_lock:
                        record = batch_inflight.get(prepared.request_key)
                        if record is None:
                            record = _BatchInFlightRecord(
                                future=asyncio.get_running_loop().create_future()
                            )
                            batch_inflight[prepared.request_key] = record
                            is_leader = True
                        else:
                            if not record.future.done():
                                record.followers += 1
                            is_leader = False

                    if not is_leader:
                        bump_ingest_stat("inflight_followers")
                        shared_document, shared_count = await record.future
                        shared = copy.deepcopy(shared_document)
                        shared.metadata["inflight_deduplicated"] = True
                        shared.metadata["inflight_shared_count"] = shared_count
                        shared.metadata.setdefault("cache_hit", False)
                        shared.metadata["requested_mode"] = prepared.requested_mode
                        documents[prepared.index] = shared
                        record_mode_result(prepared.requested_mode, success=True)
                        return True

                    bump_ingest_stat("leader_executions")
                    if execution_strategy == "isolated":
                        document = await self._execute_item_isolated(prepared)
                    else:
                        document = await self._execute_item_in_process(prepared)
                    if prepared.cache_backend is not None and prepared.cache_key is not None:
                        prepared.cache_backend.set(
                            prepared.cache_key,
                            document,
                            ttl=prepared.resolved_config.cache_ttl,
                        )

                    document.metadata["requested_mode"] = prepared.requested_mode
                    async with batch_inflight_lock:
                        shared_count = record.followers
                        shared_document = copy.deepcopy(document)
                        if not record.future.done():
                            record.future.set_result((shared_document, shared_count))
                        if batch_inflight.get(prepared.request_key) is record:
                            batch_inflight.pop(prepared.request_key, None)

                    document.metadata["inflight_deduplicated"] = False
                    document.metadata["inflight_shared_count"] = shared_count
                    document.metadata.setdefault("cache_hit", False)
                    documents[prepared.index] = document
                    record_mode_result(prepared.requested_mode, success=True)
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error_message = str(exc)
                    async with batch_inflight_lock:
                        record = batch_inflight.get(prepared.request_key)
                        if record is not None and not record.future.done() and record.followers > 0:
                            record.future.set_exception(_copy_batch_exception(exc))
                        if batch_inflight.get(prepared.request_key) is record:
                            batch_inflight.pop(prepared.request_key, None)
                    async with errors_lock:
                        errors.append(
                            BatchErrorItem(
                                index=prepared.index,
                                url=prepared.url,
                                error=error_message,
                            )
                        )
                    record_mode_result(prepared.requested_mode, success=False)
                    return False
                finally:
                    record_mode_timing(
                        prepared.requested_mode,
                        (time.perf_counter() - started_at) * 1000.0,
                    )

        tasks = [asyncio.create_task(process_url(prepared)) for prepared in prepared_requests]
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
        return SecurityReport(
            injection_score=doc.injection_score,
            risk_level=doc.metadata.get("risk_level", "UNKNOWN"),
            pattern_matches=doc.metadata.get("pattern_matches", []),
            flags=doc.flags,
            hidden_content_detected=doc.metadata.get(
                "hidden_content_detected",
                "hidden_content" in doc.flags,
            ),
            hidden_elements_count=doc.removed_elements.get("hidden_elements", 0),
            imperative_density=doc.metadata.get("imperative_density", 0.0),
            url=doc.metadata.get("url", ""),
            title=doc.metadata.get("title", ""),
            token_estimate=doc.token_estimate,
            token_reduction_percent=doc.metadata.get("token_savings", {}).get("savings_percent", 0.0),
            original_size_bytes=doc.metadata.get("original_size_bytes", 0),
            cleaned_size_bytes=doc.metadata.get("cleaned_size_bytes", len(doc.markdown.encode("utf-8"))),
            content_hash=doc.content_hash,
            structural_hash=doc.metadata.get("structural_hash", ""),
            removed_elements=doc.removed_elements,
            language=doc.metadata.get("language"),
            explanation=doc.security_explanation or {},
            observability=doc.observability or {},
        )


class CompareExtractorsUseCase:
    """Evaluate alternative extractors behind an application-level boundary."""

    def execute(self, html: str, *, model: str = "gpt-4") -> dict[str, dict[str, object]]:
        return compare_extractors(html, model=model)
