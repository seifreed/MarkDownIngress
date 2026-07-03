"""Sync HTTPX fetch path."""

from __future__ import annotations

import time
from typing import Any, cast

import httpx

from markdown_ingress.adapters.fetching.fetch_shared import (
    FetchAttemptSharedMixin,
    HttpxFetchSharedMixin,
)
from markdown_ingress.adapters.fetching.http_fetch_state import (
    HttpFetchAttemptContext,
    HttpFetchState,
)
from markdown_ingress.adapters.fetching.http_support import (
    FETCH_ATTEMPT_ERRORS,
    MAX_RETRIES,
    RETRYABLE_STATUS,
    ResponseSizeLimitError,
    retry_delay_seconds,
    validate_content_type,
)
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.models import FetchResult


class SyncHttpxFetchMixin(HttpxFetchSharedMixin, FetchAttemptSharedMixin):
    """Sync HTTPX fetch implementation."""

    def fetch_sync(self: Any, url: str) -> FetchResult:
        prepared = self._prepare_request_url_with_dns_retry(url)
        state = HttpFetchState.from_prepared_request(
            prepared,
            ssl_retried=self._is_ssl_bypass_active(prepared[4]),
        )
        client = self._get_sync_client()
        while state.attempt < MAX_RETRIES and not state.ssl_retried:
            result = self._run_sync_fetch_attempt(client, state)
            if result is not None:
                return cast(FetchResult, result)

        if state.ssl_retried and state.verify is False:
            return cast(
                FetchResult,
                self._fetch_sync_with_ssl_bypass(
                    state.to_ssl_bypass_state(),
                    consumed_attempts=state.attempt,
                    last_exc=state.last_exc,
                ),
            )

        if state.last_exc is not None:
            raise state.last_exc
        raise RuntimeError(f"Fetch failed for {state.url} after {MAX_RETRIES} attempts")

    def _run_sync_fetch_attempt(
        self: Any, client: Any, state: HttpFetchState
    ) -> FetchResult | None:
        ua, start_time = self._prepare_sync_attempt(state)
        attempt = HttpFetchAttemptContext(state=state, start_time=start_time, user_agent=ua)
        try:
            headers = self._build_headers(ua, host_header=state.host_header)
            stream = self._open_stream(
                client,
                state.url,
                headers=headers,
                sni_hostname=state.sni_hostname,
            )
            with stream as response:
                return cast(
                    FetchResult | None,
                    self._handle_sync_fetch_response(response, attempt),
                )
        except (UnsupportedContentTypeError, ResponseSizeLimitError):
            raise
        except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
            raise
        except httpx.HTTPStatusError as exc:
            self._handle_sync_status_error(state, exc)
        except (DomainCircuitOpenError, httpx.TooManyRedirects):
            raise
        except FETCH_ATTEMPT_ERRORS as exc:
            self._handle_generic_fetch_error(state, exc)
        return None

    def _handle_sync_fetch_response(
        self: Any,
        response: Any,
        attempt: HttpFetchAttemptContext,
    ) -> FetchResult | None:
        state = attempt.state
        response_host = state.host
        if self._should_follow_redirect(response):
            self._follow_sync_redirect(response, state)
            return None
        self._enforce_declared_response_size(response)
        if self._is_redirect_response(response) and not self.follow_redirects:
            content = self._read_sync_response_content(response)
            return cast(
                FetchResult,
                self._finish_fetch_result(response, attempt, content),
            )
        if response.status_code in RETRYABLE_STATUS and state.attempt < MAX_RETRIES - 1:
            self._retry_sync_response_status(response, state, response_host)
            return None
        response.raise_for_status()
        validate_content_type(response)
        content = self._read_sync_response_content(response)
        return cast(
            FetchResult,
            self._finish_fetch_result(response, attempt, content),
        )

    def _retry_sync_response_status(
        self: Any, response: Any, state: HttpFetchState, response_host: str
    ) -> None:
        retry_delay = retry_delay_seconds(response, state.attempt)
        self._handle_retryable_status(response_host, response.status_code, retry_delay)
        self._drain_sync_response_for_reuse(response, "Failed to read response body during retry")
        time.sleep(retry_delay)
        state.attempt += 1

    def _handle_sync_status_error(
        self: Any, state: HttpFetchState, exc: httpx.HTTPStatusError
    ) -> None:
        state.last_exc = exc
        status_code = exc.response.status_code
        if status_code not in RETRYABLE_STATUS:
            raise exc
        retry_delay = retry_delay_seconds(exc.response, state.attempt)
        self._handle_retryable_status(state.host, status_code, retry_delay)
        if state.attempt >= MAX_RETRIES - 1:
            raise exc
        time.sleep(retry_delay)
        state.attempt += 1
