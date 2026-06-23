"""Non-async helpers shared verbatim by the sync and async fetch mixins."""

from __future__ import annotations

import logging
import time
from typing import Any, cast

from markdown_ingress.adapters.fetching.http_fetch_state import (
    HttpFetchAttemptContext,
    HttpFetchState,
)
from markdown_ingress.adapters.fetching.http_support import should_retry_with_ssl_bypass
from markdown_ingress.adapters.fetching.response_content import FetchResultParts
from markdown_ingress.adapters.fetching.ssl_bypass_state import SslBypassAttemptContext
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)


class HttpxFetchSharedMixin:
    """Helpers identical across the sync and async HTTPX fetch mixins."""

    def _finish_fetch_result(
        self: Any,
        response: Any,
        attempt: HttpFetchAttemptContext,
        content: bytes,
    ) -> FetchResult:
        elapsed_ms = (time.perf_counter() - attempt.start_time) * 1000
        state = attempt.state
        self._record_success(state.host)
        return cast(
            FetchResult,
            self._make_fetch_result(
                FetchResultParts(
                    content=content,
                    requested_url=state.requested_logical_url,
                    final_url=state.logical_url,
                    response=response,
                    elapsed_ms=elapsed_ms,
                    user_agent=attempt.user_agent,
                    attempt=state.attempt,
                )
            ),
        )

    def _handle_generic_fetch_error(self: Any, state: HttpFetchState, exc: Exception) -> None:
        state.last_exc = exc
        self._record_failure(state.host)
        if should_retry_with_ssl_bypass(
            allow_ssl_bypass=self.allow_ssl_bypass,
            ssl_retried=state.ssl_retried,
            exc=exc,
        ):
            logger.warning(
                "SSL verification failed for %s, retrying with certificate verification "
                "disabled. "
                "This bypass is insecure and should only be used for testing.",
                state.url,
            )
            state.mark_ssl_bypass_retry()
            return
        state.attempt += 1


class SslBypassSharedMixin:
    """Helpers identical across the sync and async SSL-bypass fetch mixins."""

    def _finish_ssl_bypass_result(
        self: Any,
        response: Any,
        attempt: SslBypassAttemptContext,
        content: bytes,
    ) -> FetchResult:
        elapsed_ms = (time.perf_counter() - attempt.start_time) * 1000
        state = attempt.state
        self._record_success(state.host)
        self._remember_ssl_bypass_host(state.host)
        return cast(
            FetchResult,
            self._make_fetch_result(
                FetchResultParts(
                    content=content,
                    requested_url=state.requested_logical_url,
                    final_url=state.logical_url,
                    response=response,
                    elapsed_ms=elapsed_ms,
                    user_agent=attempt.user_agent,
                    attempt=attempt.ssl_attempt,
                    ssl_bypass=True,
                    total_attempt=attempt.total_attempt,
                )
            ),
        )
