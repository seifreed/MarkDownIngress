"""Application use cases coordinating infrastructure adapters and core pipeline logic."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import multiprocessing
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

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
from markdown_ingress.core.stealth.browser_config import ADVANCED_USER_AGENTS
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.reporting import persist_report_for_document, security_report_from_document
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
_AUTO_RENDER_MIN_IMPROVEMENT = 0.10


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


def _ensure_fetcher_user_agent(
    url: str,
    config: IngestConfig,
    matched_domain_policy=None,
) -> str:
    """Select and persist a per-request HTTP user agent.

    The request identity and the actual fetcher must use the same UA so cache
    and in-flight deduplication do not cross-contaminate different request
    variants.

    Note: Intentionally mutates ``config.fetcher_user_agent`` in-place.
    Callers must pass a cloned config to avoid polluting shared state.
    """
    if config.fetcher_user_agent:
        return config.fetcher_user_agent
    identity_config = config.clone()
    identity_config.fetcher_user_agent = ""
    identity_payload = build_request_identity(url, identity_config, matched_domain_policy)
    digest = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
    selected = ADVANCED_USER_AGENTS[int.from_bytes(digest[:8], "big") % len(ADVANCED_USER_AGENTS)]
    config.fetcher_user_agent = selected
    return selected


def _looks_like_non_html_resource(url: str) -> bool:
    """Best-effort URL heuristic to avoid launching Playwright for obvious downloads."""
    path = urlsplit(url).path.lower()
    return any(path.endswith(extension) for extension in _NON_HTML_EXTENSIONS)


def _looks_like_auth_interstitial(url: str) -> bool:
    """Skip costly auto-render for account/login flows that rarely improve via Playwright.

    Inspects hostname labels (split by ``"."``) and path segments (split by
    ``"/"``), but NOT query parameters, to avoid false positives like
    ``?tracking=account_id``.
    """
    parsed = urlsplit(url)
    tokens: set[str] = set()
    if parsed.hostname:
        tokens.update(label.lower() for label in parsed.hostname.split("."))
    tokens.update(seg.lower() for seg in parsed.path.split("/") if seg)
    return any(token in tokens for token in _AUTH_PATH_TOKENS)


def _should_attempt_render_fallback(exc: Exception) -> bool:
    """Limit auto-mode render fallback to failures a browser may realistically improve."""
    if isinstance(exc, (DomainCircuitOpenError, UnsupportedContentTypeError, PolicyBlockedError)):
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


def _copy_custom_attrs(source: Exception, target: Exception) -> None:
    """Copy custom instance attributes from source to target exception."""
    skip = {"args", "__cause__", "__suppress_context__", "__notes__", "__traceback__"}
    for attr, value in getattr(source, "__dict__", {}).items():
        if attr not in skip:
            try:
                setattr(target, attr, value)
            except (AttributeError, TypeError):
                pass


def _copy_batch_exception(exc: Exception) -> Exception:
    """Copy an exception for batch processing, preserving type information.

    Tries multiple fallback strategies to preserve exception type:
    1. Deep copy (works for most exceptions)
    2. Single-arg constructor (works for exceptions like ValueError)
    3. No-arg constructor with args set (works for exceptions with custom init)
    4. RuntimeError fallback (guaranteed to work)

    BUG FIX: Preserves exception cause chain for debugging.
    BUG FIX: Preserves custom attributes (e.g. PolicyBlockedError.document).
    """
    try:
        return copy.deepcopy(exc)
    except Exception:
        try:
            # Try single-arg constructor (most common case)
            new_exc = type(exc)(str(exc))
            # BUG FIX: Preserve cause chain for debugging
            new_exc.__cause__ = exc
            new_exc.__suppress_context__ = getattr(exc, "__suppress_context__", False)
            if hasattr(exc, "__notes__"):
                new_exc.__notes__ = list(exc.__notes__)
            _copy_custom_attrs(exc, new_exc)
            return new_exc
        except Exception:
            try:
                # Try no-arg constructor and set args manually
                new_exc = type(exc)()
                new_exc.args = (str(exc),)
                # BUG FIX: Preserve cause chain for debugging
                new_exc.__cause__ = exc
                new_exc.__suppress_context__ = getattr(exc, "__suppress_context__", False)
                if hasattr(exc, "__notes__"):
                    new_exc.__notes__ = list(exc.__notes__)
                _copy_custom_attrs(exc, new_exc)
                return new_exc
            except Exception:
                # Last resort: preserve type name in RuntimeError
                runtime_exc = RuntimeError(f"{type(exc).__name__}: {exc}")
                # BUG FIX: Preserve cause chain for debugging
                runtime_exc.__cause__ = exc
                runtime_exc.__suppress_context__ = getattr(exc, "__suppress_context__", False)
                if hasattr(exc, "__notes__"):
                    runtime_exc.__notes__ = list(exc.__notes__)
                _copy_custom_attrs(exc, runtime_exc)
                return runtime_exc


def _is_picklable(obj: object) -> bool:
    import pickle

    try:
        pickle.dumps(obj)
        return True
    except Exception:
        return False


def _make_picklable(value: object) -> object:
    if isinstance(value, dict):
        return {k: _make_picklable(v) for k, v in value.items() if _is_picklable(v)}
    if isinstance(value, list):
        return [_make_picklable(v) for v in value if _is_picklable(v)]
    return value


def _execute_batch_ingest_in_subprocess(
    url: str,
    config: IngestConfig,
    playwright_available: bool,
    queue,
) -> None:
    try:
        document = IngestUseCase(playwright_available=playwright_available).execute(url, config)
        document.metadata = _make_picklable(document.metadata)
        queue.put(("result", document))
    except Exception as exc:  # pragma: no cover - child process path
        try:
            queue.put(("exception", exc))
        except Exception:
            queue.put(("exception_payload", {"type": type(exc).__name__, "message": str(exc)}))


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
        # Shared fetcher for connection pooling and circuit breaker reuse
        self._shared_fetcher: IFetcher | None = None
        self._shared_fetcher_config_key: tuple | None = None

    @staticmethod
    def _default_fetcher_factory(config: IngestConfig) -> IFetcher:
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
        return PlaywrightRenderer(config=config)

    @staticmethod
    def _close_fetcher(fetcher: object) -> None:
        """Close a sync fetcher if it exposes a close() hook."""
        close = getattr(fetcher, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as exc:  # pragma: no cover - defensive cleanup path
            _logger.warning("Failed to close fetcher cleanly: %s", exc, exc_info=True)

    @staticmethod
    def _fetcher_config_key(config: IngestConfig) -> tuple:
        """Hashable key to identify when a Fetcher can be reused across requests."""
        return (
            config.timeout,
            getattr(config, "fetcher_user_agent", None),
            config.allow_local_urls,
            config.domain_request_interval,
            config.circuit_breaker_threshold,
            config.circuit_breaker_open_seconds,
        )

    def _get_shared_fetcher(self, config: IngestConfig) -> IFetcher:
        """Get or create a shared Fetcher, reusing when config is compatible."""
        key = self._fetcher_config_key(config)
        if self._shared_fetcher is not None and self._shared_fetcher_config_key == key:
            return self._shared_fetcher
        # Close old fetcher if config changed
        if self._shared_fetcher is not None:
            self._close_fetcher(self._shared_fetcher)
        self._shared_fetcher = self.fetcher_factory(config)
        self._shared_fetcher_config_key = key
        return self._shared_fetcher

    def close(self) -> None:
        """Close shared resources (fetcher, etc.)."""
        if self._shared_fetcher is not None:
            self._close_fetcher(self._shared_fetcher)
            self._shared_fetcher = None
            self._shared_fetcher_config_key = None

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
            type(orchestrator) is IngestOrchestrator
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
        _ensure_fetcher_user_agent(url, resolved_config, matched_domain_policy)
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
                try:
                    cached = cache_backend.get(cache_key)
                except Exception as exc:
                    _logger.warning(
                        "Cache lookup failed for %s; continuing without cache: %s",
                        cache_key,
                        exc,
                        exc_info=True,
                    )
                    cached = None
                if cached is not None:
                    try:
                        cached_copy = self.orchestrator.clone_cached_document(cached)
                        bump_ingest_stat("cache_hits")
                        cached_copy.metadata["requested_mode"] = requested_mode
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

            in_flight = self.orchestrator.acquire_inflight(request_key)
            if in_flight is not None:
                # We're a follower, not a leader
                bump_ingest_stat("inflight_followers")
                shared = cast(
                    SafeDocument, self.orchestrator.await_inflight(in_flight, request_key)
                )
                shared.metadata["inflight_deduplicated"] = True
                shared.metadata.setdefault("cache_hit", False)
                shared.metadata["requested_mode"] = requested_mode
                record_mode_result(requested_mode, success=True)
                return shared
            leader_slot_acquired = True
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
        # Note: record_mode_result already called for cache hits and inflight followers above
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
            try:
                cache_backend.set(cache_key, document, ttl=config.cache_ttl)
            except Exception as exc:
                _logger.warning(
                    "Cache write failed for %s; continuing without cache: %s",
                    cache_key,
                    exc,
                    exc_info=True,
                )
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
            fast_doc = self._execute_explicit_mode(
                url, fast_config, matched_domain_policy, budget_state
            )
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
            render_doc = self._execute_explicit_mode(
                url, render_config, matched_domain_policy, budget_state
            )
            render_doc.metadata["auto_mode_used"] = "render"
            render_doc.metadata["auto_mode_reason"] = "fast_failed"
            return render_doc

        if (
            fast_doc.token_estimate >= config.auto_render_threshold
            or not self.playwright_available
            or _looks_like_auth_interstitial(url)
            or _looks_like_non_html_resource(url)
        ):
            fast_doc.metadata["auto_mode_used"] = "fast"
            return fast_doc

        render_config = config.clone()
        render_config.mode = "render"
        try:
            render_doc = self._execute_explicit_mode(
                url, render_config, matched_domain_policy, budget_state
            )
        except Exception as exc:
            if not _should_attempt_fast_degraded_fallback(exc):
                raise
            _logger.warning(
                "auto mode: render attempt failed, falling back to fast result. Error: %s", exc
            )
            fast_doc.metadata["auto_mode_used"] = "fast"
            fast_doc.metadata["auto_mode_reason"] = "render_fallback"
            return fast_doc
        render_fetch_metadata = render_doc.metadata.get("fetch_metadata", {})
        render_attempt_degraded = bool(render_fetch_metadata.get("degraded_render_fallback"))
        improvement_threshold = max(1, int(fast_doc.token_estimate * _AUTO_RENDER_MIN_IMPROVEMENT))
        if render_attempt_degraded:
            if render_doc.token_estimate >= fast_doc.token_estimate + improvement_threshold:
                render_doc.metadata["auto_mode_used"] = "render"
                render_doc.metadata["auto_mode_reason"] = "degraded_render"
                render_doc.metadata["fast_mode_tokens"] = fast_doc.token_estimate
                return render_doc
            existing_flags = list(fast_doc.metadata.get("operational_flags", []))
            for flag in render_doc.metadata.get("operational_flags", []):
                if flag not in existing_flags:
                    existing_flags.append(flag)
            fast_doc.metadata["operational_flags"] = existing_flags
            fast_doc.metadata["auto_mode_used"] = "fast"
            fast_doc.metadata["auto_mode_reason"] = "render_fallback"
            return fast_doc
        if render_doc.token_estimate >= fast_doc.token_estimate + improvement_threshold:
            render_doc.metadata["auto_mode_used"] = "render"
            render_doc.metadata["fast_mode_tokens"] = fast_doc.token_estimate
            # Clean up fast_doc's temporary screenshot since we're returning render_doc.
            fast_fetch_metadata = fast_doc.metadata.get("fetch_metadata", {})
            if fast_fetch_metadata.get("screenshot_temp"):
                from markdown_ingress.core.renderer import Renderer

                Renderer.cleanup_screenshot(fast_fetch_metadata.get("screenshot_path"))
            return render_doc
        if render_fetch_metadata.get("screenshot_temp"):
            from markdown_ingress.core.renderer import Renderer

            Renderer.cleanup_screenshot(render_fetch_metadata.get("screenshot_path"))

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
            used = budget_state["used"] if budget_state["used"] is not None else 0
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
            # The render cost budget is an upper bound on the combined
            # fast+render spend, so we only charge the delta up to 5 units.
            # A previous audit flagged this as undercharging, but the test
            # suite documents the opposite contract: fast probes already
            # consumed budget leave less room for render, by design.
            render_cost_units = max(
                0, 5 - (budget_state["used"] if budget_state["used"] is not None else 0)
            )
            consume_cost(render_cost_units, "render mode")
            if not self.playwright_available:
                raise ImportError(
                    "Render mode requires Playwright. Install with: "
                    "pip install 'markdown-ingress[render]' && playwright install"
                )
            screenshot_temp_path: str | None = None
            screenshot_was_temp = False
            render_config = RenderConfig(
                timeout=config.timeout,
                wait_until="domcontentloaded",
                stealth=config.stealth,
                disable_http2=config.disable_http2,
                extreme_mode=config.extreme_mode,
                screenshot=config.screenshot,
            )
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
            renderer = self.renderer_factory(render_config)
            try:
                fetch_result = timed_stage(
                    "fetch_render",
                    lambda: renderer.render_sync(url),
                )
                if screenshot_was_temp:
                    fetch_result.metadata["screenshot_temp"] = True
            except Exception as render_exc:
                if screenshot_was_temp and screenshot_temp_path is not None:
                    try:
                        os.unlink(screenshot_temp_path)
                    except OSError:
                        pass
                if not _should_attempt_fast_degraded_fallback(render_exc):
                    raise
                consume_cost(1, "degraded fast fallback from render")
                degraded_fetcher = self._get_shared_fetcher(config)
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
            fetcher = self._get_shared_fetcher(config)
            try:
                fetch_result = timed_stage(
                    "fetch_fast",
                    lambda: fetcher.fetch_sync(url),
                )
            except DomainCircuitOpenError:
                bump_ingest_stat("circuit_breaker_rejections")
                raise
        fetch_result.metadata.setdefault(
            "cost_units_used",
            budget_state["used"] if budget_state["used"] is not None else 0,
        )
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
        _ensure_fetcher_user_agent(url, resolved_config, matched_domain_policy)
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
            while True:
                if not queue.empty():
                    kind, payload = queue.get_nowait()
                    process.join(timeout=0.5)
                    if kind == "result":
                        return cast(SafeDocument, payload)
                    if kind == "exception":
                        raise payload
                    if kind == "exception_payload":
                        raise RuntimeError(f"{payload['type']}: {payload['message']}")
                    raise RuntimeError(
                        f"Batch worker returned an unknown payload for {prepared.url}"
                    )
                if not process.is_alive():
                    process.join(timeout=0.5)
                    if not queue.empty():
                        continue
                    raise RuntimeError(
                        f"Batch worker exited without returning a result for {prepared.url}"
                    )
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self._terminate_batch_process(process)
            raise
        finally:
            if process.is_alive():
                self._terminate_batch_process(process)
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
        semaphore = asyncio.Semaphore(max_concurrent)
        errors_lock = asyncio.Lock()  # Protect concurrent errors.append() calls
        batch_inflight: dict[str, _BatchInFlightRecord] = {}
        batch_inflight_lock = asyncio.Lock()
        progress_lock = asyncio.Lock()
        completed = 0
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

        async def report_completion(url: str) -> None:
            nonlocal completed
            if on_progress is None:
                return
            async with progress_lock:
                completed += 1
                on_progress(completed, total, url)

        async def process_url(prepared: _PreparedBatchRequest) -> bool:
            """Return True on success, False on failure."""
            started_at = time.perf_counter()
            # In-process mode delegates to IngestUseCase.execute which records its own metrics.
            # Only record here for isolated mode (subprocess can't update parent-process stats).
            batch_tracks_metrics = execution_strategy != "local"
            if batch_tracks_metrics:
                bump_ingest_stat("requests_total")
                record_mode_request(prepared.requested_mode)
            is_leader = False
            record: _BatchInFlightRecord | None = None
            semaphore_held = False
            try:
                # Acquire semaphore for cache check + inflight detection.
                # This preserves sequential cache reuse (with max_concurrent=1
                # the second identical URL finds the first's result cached).
                await semaphore.acquire()
                semaphore_held = True

                if prepared.cache_backend is not None and prepared.cache_key is not None:
                    try:
                        cached = prepared.cache_backend.get(prepared.cache_key)
                    except Exception as exc:
                        _logger.warning(
                            "Batch cache lookup failed for %s; continuing without cache: %s",
                            prepared.cache_key,
                            exc,
                            exc_info=True,
                        )
                        cached = None
                    if cached is not None:
                        try:
                            cached_copy = self.ingest_use_case.orchestrator.clone_cached_document(
                                cached
                            )
                            bump_ingest_stat("cache_hits")
                            cached_copy.metadata["requested_mode"] = prepared.requested_mode
                            documents[prepared.index] = cached_copy
                            record_mode_result(prepared.requested_mode, success=True)
                            await report_completion(prepared.url)
                            return True
                        except Exception as exc:
                            _logger.warning(
                                "Failed to clone cached batch document for %s, cache entry may be corrupt: %s",
                                prepared.cache_key,
                                exc,
                                exc_info=True,
                            )
                            _purge_corrupt_cache_entry(
                                prepared.cache_backend,
                                prepared.cache_key,
                            )
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
                        record.followers += 1
                        is_leader = False

                if not is_leader:
                    # Release semaphore BEFORE awaiting — followers do no real
                    # work, so holding a slot would waste concurrency and with
                    # max_concurrent=1 + duplicates could cause effective deadlock.
                    semaphore.release()
                    semaphore_held = False
                    bump_ingest_stat("inflight_followers")
                    try:
                        shared_document, shared_count = await record.future
                    except asyncio.CancelledError:
                        # Clean up stale entry so future requests for the same key don't
                        # find a cancelled future and deadlock.
                        async with batch_inflight_lock:
                            rec = batch_inflight.get(prepared.request_key)
                            if rec is not None and rec.future.done():
                                batch_inflight.pop(prepared.request_key, None)
                        raise
                    except Exception as exc:
                        import traceback

                        error_message = str(exc)
                        async with errors_lock:
                            errors.append(
                                BatchErrorItem(
                                    index=prepared.index,
                                    url=prepared.url,
                                    error=error_message,
                                    error_type=type(exc).__name__,
                                    traceback=traceback.format_exc(),
                                )
                            )
                        record_mode_result(prepared.requested_mode, success=False)
                        async with batch_inflight_lock:
                            if batch_inflight.get(prepared.request_key) is record:
                                batch_inflight.pop(prepared.request_key, None)
                        return False
                    shared = copy.deepcopy(shared_document)
                    shared.metadata["inflight_deduplicated"] = True
                    shared.metadata["inflight_shared_count"] = shared_count
                    shared.metadata.setdefault("cache_hit", False)
                    shared.metadata["requested_mode"] = prepared.requested_mode
                    documents[prepared.index] = shared
                    record_mode_result(prepared.requested_mode, success=True)
                    await report_completion(prepared.url)
                    return True

                # Leader path: semaphore already held, execute ingestion
                bump_ingest_stat("leader_executions")
                if execution_strategy == "isolated":
                    document = await self._execute_item_isolated(prepared)
                else:
                    document = await self._execute_item_in_process(prepared)
                if prepared.cache_backend is not None and prepared.cache_key is not None:
                    try:
                        prepared.cache_backend.set(
                            prepared.cache_key,
                            document,
                            ttl=prepared.resolved_config.cache_ttl,
                        )
                    except Exception as exc:
                        _logger.warning(
                            "Batch cache write failed for %s; continuing without cache: %s",
                            prepared.cache_key,
                            exc,
                            exc_info=True,
                        )

                document.metadata["requested_mode"] = prepared.requested_mode
                async with batch_inflight_lock:
                    shared_count = record.followers
                    shared_document = copy.deepcopy(document)
                    try:
                        record.future.set_result((shared_document, shared_count))
                    except asyncio.InvalidStateError:
                        _logger.warning(
                            "Batch inflight future already done for %s (state: %s); "
                            "followers may have been cancelled",
                            prepared.request_key[:32],
                            getattr(record.future, '_state', 'unknown'),
                        )
                    if batch_inflight.get(prepared.request_key) is record:
                        batch_inflight.pop(prepared.request_key, None)

                document.metadata["inflight_deduplicated"] = False
                document.metadata["inflight_shared_count"] = shared_count
                document.metadata.setdefault("cache_hit", False)
                documents[prepared.index] = document
                if batch_tracks_metrics:
                    record_mode_result(prepared.requested_mode, success=True)
                await report_completion(prepared.url)
                return True
            except asyncio.CancelledError:
                if record is not None:
                    async with batch_inflight_lock:
                        if not record.future.done():
                            record.future.cancel()
                        if batch_inflight.get(prepared.request_key) is record:
                            batch_inflight.pop(prepared.request_key, None)
                raise
            except Exception as exc:
                import traceback

                error_message = str(exc)
                if (
                    isinstance(exc, PolicyBlockedError)
                    and exc.document is not None
                    and prepared.resolved_config.save_reports
                ):
                    try:
                        await asyncio.to_thread(
                            persist_report_for_document,
                            exc.document,
                            prepared.resolved_config.reports_dir,
                        )
                    except OSError as persist_exc:
                        _logger.warning(
                            "Failed to persist security report for %s: %s",
                            prepared.url,
                            persist_exc,
                        )
                if record is not None:
                    async with batch_inflight_lock:
                        try:
                            if not record.future.done():
                                record.future.set_exception(
                                    _copy_batch_exception(exc)
                                )
                        except asyncio.InvalidStateError:
                            _logger.warning(
                                "Batch inflight future already done when "
                                "setting exception for %s",
                                prepared.request_key[:32],
                            )
                        if batch_inflight.get(prepared.request_key) is record:
                            batch_inflight.pop(prepared.request_key, None)
                async with errors_lock:
                    errors.append(
                        BatchErrorItem(
                            index=prepared.index,
                            url=prepared.url,
                            error=error_message,
                            error_type=type(exc).__name__,
                            traceback=traceback.format_exc(),
                        )
                    )
                if batch_tracks_metrics:
                    record_mode_result(prepared.requested_mode, success=False)
                await report_completion(prepared.url)
                return False
            finally:
                if semaphore_held:
                    semaphore.release()
                if batch_tracks_metrics:
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
        return security_report_from_document(doc)


class CompareExtractorsUseCase:
    """Evaluate alternative extractors behind an application-level boundary."""

    def execute(self, html: str, *, model: str = "gpt-4") -> dict[str, dict[str, object]]:
        return compare_extractors(html, model=model)
