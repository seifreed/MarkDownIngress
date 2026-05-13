"""Fetch/render execution pipeline for single URL ingestion."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from typing import Any, cast

from markdown_ingress.application.batch_processor import _CostBudget
from markdown_ingress.application.heuristics import (
    _looks_like_auth_interstitial,
    _looks_like_non_html_resource,
    _should_attempt_fast_degraded_fallback,
    _should_attempt_render_fallback,
)
from markdown_ingress.config_models import DomainPolicy, IngestConfig, RenderConfig
from markdown_ingress.core.ingest_stats import bump_ingest_stat, timed_stage_with_snapshot
from markdown_ingress.core.interfaces import IFetcher, IIngestOrchestrator, IRenderer
from markdown_ingress.core.metadata_keys import (
    AUTO_MODE_REASON,
    AUTO_MODE_USED,
    COST_UNITS_USED,
    DEGRADED_REASON,
    DEGRADED_RENDER_FALLBACK,
    EFFECTIVE_MODE,
    FAST_MODE_TOKENS,
    FETCH_METADATA,
    OPERATIONAL_FLAGS,
    RENDER_COST_BUDGET,
    SCREENSHOT_TEMP,
)
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.core.ssrf import (
    dns_pin_for_validated_http_url,
    resolve_allow_local_urls,
    validate_http_url_no_ssrf_with_dns_check,
)
from markdown_ingress.models import FetchResult, SafeDocument

_logger = logging.getLogger(__name__)

_AUTO_RENDER_MIN_IMPROVEMENT = 0.10
_RENDER_COST_BUDGET_CEILING: int = 5


def _cleanup_screenshot(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _cleanup_orphaned_screenshot(config: IngestConfig | RenderConfig) -> None:
    """Remove temporary screenshot file from a failed render attempt."""
    screenshot = config.screenshot
    if screenshot is True:
        return  # auto-temp path: renderer handles cleanup
    if isinstance(screenshot, str) and os.path.isfile(screenshot):
        # Only delete files that actually live in a temporary directory to
        # avoid destroying user-provided paths.
        abs_path = os.path.abspath(screenshot)
        tmp_root = os.path.abspath(tempfile.gettempdir())
        if not abs_path.startswith(tmp_root + os.sep):
            return
        try:
            os.unlink(screenshot)
        except OSError as exc:
            _logger.debug("Could not remove screenshot %s: %s", screenshot, exc)


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
    def _validate_render_url(url: str, config: IngestConfig) -> tuple[str, dict[str, str]]:
        """Apply SSRF validation and return the logical URL plus browser DNS pins."""
        logical_url = str(url).strip()
        validated_url = validate_http_url_no_ssrf_with_dns_check(
            logical_url,
            allow_local=resolve_allow_local_urls(config.allow_local_urls),
        )
        pin = dns_pin_for_validated_http_url(logical_url, validated_url)
        dns_pins = {pin[0]: pin[1]} if pin is not None else {}
        return logical_url, dns_pins

    def execute_mode(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        budget: _CostBudget,
    ) -> SafeDocument:
        fetch_result, operational_flags = self._fetch_result(url, config, budget)
        return cast(
            SafeDocument,
            self._orchestrator.process_fetched_content(
                fetch_result=fetch_result,
                config=config,
                matched_domain_policy=matched_domain_policy,
                operational_flags=operational_flags,
            ),
        )

    def _fetch_result(
        self, url: str, config: IngestConfig, budget: _CostBudget
    ) -> tuple[FetchResult, list[str]]:
        operational_flags: list[str] = []
        stage_timings: dict[str, float] = {}

        def timed_stage(stage: str, fn: Callable[[], Any]) -> Any:
            return timed_stage_with_snapshot(stage_timings, stage, fn)

        if config.mode == "render":
            try:
                fetch_result = self._fetch_render(
                    url, config, budget, timed_stage, operational_flags
                )
            except Exception:
                _cleanup_orphaned_screenshot(config)
                raise
        else:
            fetch_result = self._fetch_fast(url, config, budget, timed_stage)

        fetch_result.metadata.setdefault(COST_UNITS_USED, budget.used)
        fetch_result.metadata.setdefault(RENDER_COST_BUDGET, budget.limit)
        fetch_result.metadata.setdefault("stage_timings_ms", stage_timings)
        return fetch_result, operational_flags

    @staticmethod
    def _prepare_render_config(
        config: IngestConfig,
        dns_pins: dict[str, str] | None = None,
    ) -> tuple[RenderConfig, str | None, bool]:
        """Build a RenderConfig, allocating a temp screenshot file when screenshot=True."""
        render_config = RenderConfig(
            timeout=config.timeout,
            wait_until="domcontentloaded",
            stealth=config.stealth,
            disable_http2=config.disable_http2,
            extreme_mode=config.extreme_mode,
            screenshot=config.screenshot,
            allow_local_urls=config.allow_local_urls,
            dns_pins=dict(dns_pins or {}),
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
        try:
            budget.consume(1, "degraded fast fallback from render")
        except RuntimeError:
            _logger.warning(
                "Budget exceeded during degraded fallback; proceeding without budget charge"
            )
        fetcher = self._get_shared_fetcher(config)
        fetch_result = cast(
            FetchResult, timed_stage("fetch_fast_degraded", lambda: fetcher.fetch_sync(url))
        )
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
            fetch_result = cast(
                FetchResult, timed_stage("fetch_render", lambda: renderer.render_sync(url))
            )
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
        render_url, dns_pins = self._validate_render_url(url, config)
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
            config,
            dns_pins,
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
            return cast(FetchResult, timed_stage("fetch_fast", lambda: fetcher.fetch_sync(url)))
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
        except Exception as render_exc:
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

        def _merge_operational_flags(target_doc: SafeDocument, source_doc: SafeDocument) -> None:
            existing_flags = list(target_doc.metadata.get(OPERATIONAL_FLAGS, []))
            for flag in source_doc.metadata.get(OPERATIONAL_FLAGS, []):
                if flag not in existing_flags:
                    existing_flags.append(flag)
            target_doc.metadata[OPERATIONAL_FLAGS] = existing_flags

        if render_attempt_degraded:
            if render_doc.token_estimate >= fast_doc.token_estimate + improvement_threshold:
                render_doc.metadata[AUTO_MODE_USED] = "render"
                render_doc.metadata[AUTO_MODE_REASON] = "degraded_render"
                render_doc.metadata[FAST_MODE_TOKENS] = fast_doc.token_estimate
                _merge_operational_flags(render_doc, fast_doc)
                return render_doc
            _merge_operational_flags(fast_doc, render_doc)
            fast_doc.metadata[AUTO_MODE_USED] = "fast"
            fast_doc.metadata[AUTO_MODE_REASON] = "render_fallback"
            fast_doc.metadata[FAST_MODE_TOKENS] = fast_doc.token_estimate
            return fast_doc
        if render_doc.token_estimate >= fast_doc.token_estimate + improvement_threshold:
            render_doc.metadata[AUTO_MODE_USED] = "render"
            render_doc.metadata[FAST_MODE_TOKENS] = fast_doc.token_estimate
            _merge_operational_flags(render_doc, fast_doc)
            fast_fetch_metadata = fast_doc.metadata.get(FETCH_METADATA, {})
            if fast_fetch_metadata.get(SCREENSHOT_TEMP):
                _cleanup_screenshot(fast_fetch_metadata.get("screenshot_path"))
            return render_doc
        if render_fetch_metadata.get(SCREENSHOT_TEMP):
            _cleanup_screenshot(render_fetch_metadata.get("screenshot_path"))
        fast_doc.metadata[AUTO_MODE_USED] = "fast"
        return fast_doc
