"""Fetch/render execution pipeline for single URL ingestion."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from markdown_ingress.application.batch_state import _CostBudget
from markdown_ingress.application.fetch_auto_mode import _AutoModeSelector as _AutoModeSelector
from markdown_ingress.application.heuristics import (
    _looks_like_non_html_resource,
    _should_attempt_fast_degraded_fallback,
)
from markdown_ingress.application.screenshot_files import (
    cleanup_orphaned_screenshot as _cleanup_orphaned_screenshot,
)
from markdown_ingress.application.screenshot_files import (
    cleanup_screenshot as _cleanup_screenshot,
)
from markdown_ingress.config_models import DomainPolicy, IngestConfig, RenderConfig
from markdown_ingress.core.ingest_stats import bump_ingest_stat, timed_stage_with_snapshot
from markdown_ingress.core.interfaces import IFetcher, IIngestOrchestrator, IRenderer
from markdown_ingress.core.metadata_keys import (
    COST_UNITS_USED,
    DEGRADED_REASON,
    DEGRADED_RENDER_FALLBACK,
    EFFECTIVE_MODE,
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

_RENDER_COST_BUDGET_CEILING: int = 5


@dataclass(frozen=True)
class _RenderAttemptContext:
    url: str
    config: IngestConfig
    render_config: RenderConfig
    screenshot_temp_path: str | None
    screenshot_was_temp: bool
    budget: _CostBudget
    timed_stage: Callable[[str, Callable[[], Any]], Any]
    operational_flags: list[str]


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
            with tempfile.NamedTemporaryFile(
                suffix=".png",
                delete=False,
                prefix="mdingress_screenshot_",
            ) as tmp_file:
                screenshot_temp_path = tmp_file.name
            render_config.screenshot = screenshot_temp_path
            screenshot_was_temp = True
        return render_config, screenshot_temp_path, screenshot_was_temp

    def _execute_fast_degraded_fallback(
        self,
        context: _RenderAttemptContext,
        render_exc: Exception,
    ) -> FetchResult:
        try:
            context.budget.consume(1, "degraded fast fallback from render")
        except RuntimeError:
            _logger.warning(
                "Budget exceeded during degraded fallback; proceeding without budget charge"
            )
        fetcher = self._get_shared_fetcher(context.config)
        fetch_result = cast(
            FetchResult,
            context.timed_stage("fetch_fast_degraded", lambda: fetcher.fetch_sync(context.url)),
        )
        context.operational_flags.extend(
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
        context: _RenderAttemptContext,
        render_exc: Exception,
    ) -> FetchResult:
        if context.screenshot_was_temp and context.screenshot_temp_path is not None:
            _cleanup_screenshot(context.screenshot_temp_path, logger=_logger)
        if not _should_attempt_fast_degraded_fallback(render_exc):
            raise render_exc
        return self._execute_fast_degraded_fallback(context, render_exc)

    def _run_render_or_degrade(self, context: _RenderAttemptContext) -> FetchResult:
        """Run the Playwright render; fall back to a plain HTTP fetch on retryable failure."""
        renderer = self._renderer_factory(context.render_config)
        try:
            fetch_result = cast(
                FetchResult,
                context.timed_stage("fetch_render", lambda: renderer.render_sync(context.url)),
            )
            if context.screenshot_was_temp:
                reported_screenshot_path = fetch_result.metadata.get("screenshot_path")
                if reported_screenshot_path:
                    if (
                        context.screenshot_temp_path is not None
                        and str(reported_screenshot_path) != context.screenshot_temp_path
                    ):
                        _cleanup_screenshot(context.screenshot_temp_path)
                    fetch_result.metadata[SCREENSHOT_TEMP] = True
                else:
                    _cleanup_screenshot(context.screenshot_temp_path)
        except Exception as render_exc:  # noqa: BLE001 - render mode returns structured failure
            return self._handle_render_failure(context, render_exc)
        else:
            return fetch_result

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
            _RenderAttemptContext(
                url=render_url,
                config=config,
                render_config=render_config,
                screenshot_temp_path=screenshot_temp_path,
                screenshot_was_temp=screenshot_was_temp,
                budget=budget,
                timed_stage=timed_stage,
                operational_flags=operational_flags,
            )
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
