"""Auto-mode selection for single URL ingestion."""

from __future__ import annotations

import logging
from typing import Protocol

from markdown_ingress.application.batch_state import _CostBudget
from markdown_ingress.application.heuristics import (
    _looks_like_auth_interstitial,
    _looks_like_non_html_resource,
    _should_attempt_fast_degraded_fallback,
    _should_attempt_render_fallback,
)
from markdown_ingress.application.screenshot_files import cleanup_screenshot
from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.metadata_keys import (
    AUTO_MODE_REASON,
    AUTO_MODE_USED,
    DEGRADED_RENDER_FALLBACK,
    FAST_MODE_TOKENS,
    FETCH_METADATA,
    OPERATIONAL_FLAGS,
    SCREENSHOT_TEMP,
)
from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)

_AUTO_RENDER_MIN_IMPROVEMENT = 0.10


class _IngestPipeline(Protocol):
    def execute_mode(
        self,
        url: str,
        config: IngestConfig,
        matched_domain_policy: DomainPolicy | None,
        budget: _CostBudget,
    ) -> SafeDocument: ...


class _AutoModeSelector:
    """Orchestrates auto-mode: fast probe, render evaluation, and winner selection."""

    def __init__(self, pipeline: _IngestPipeline, playwright_available: bool) -> None:
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
                cleanup_screenshot(fast_fetch_metadata.get("screenshot_path"))
            return render_doc
        if render_fetch_metadata.get(SCREENSHOT_TEMP):
            cleanup_screenshot(render_fetch_metadata.get("screenshot_path"))
        fast_doc.metadata[AUTO_MODE_USED] = "fast"
        return fast_doc
