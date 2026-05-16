"""Sync SSL-bypass fetch retry path."""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import httpx

from markdown_ingress.adapters.fetching.http_support import (
    MAX_RETRIES,
    RETRYABLE_STATUS,
    ResponseSizeLimitError,
    retry_delay_seconds,
    ssl_bypass_retry_delay,
    validate_content_type,
)
from markdown_ingress.adapters.fetching.ssl_bypass_state import (
    SslBypassFetchState,
    raise_ssl_bypass_exhausted,
)
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)


class SyncSslBypassFetchMixin:
    """Certificate-verification bypass retry path for sync fetch operations."""

    def _fetch_sync_with_ssl_bypass(
        self: Any,
        state: SslBypassFetchState,
        *,
        consumed_attempts: int,
        last_exc: Exception | None,
    ) -> FetchResult:
        remaining_attempts = max(0, MAX_RETRIES - consumed_attempts)
        if remaining_attempts == 0:
            raise_ssl_bypass_exhausted(state.url, last_exc)

        ssl_last_exc: Exception | None = None
        ssl_attempt = 0
        while ssl_attempt < remaining_attempts:
            ssl_attempt_num = ssl_attempt + 1
            total_attempt_num = consumed_attempts + ssl_attempt_num
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=False,
                max_redirects=self.max_redirects,
                verify=self._build_ssl_context(verify_certificates=False),
                trust_env=False,
            ) as client:
                try:
                    while True:
                        ua = self._next_user_agent(previous=state.previous_ua)
                        state.previous_ua = ua
                        self._ensure_circuit_closed(state.host)
                        sleep_for = self._reserve_domain_slot(state.host)
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                            self._ensure_circuit_closed(state.host)
                        start_time = time.perf_counter()

                        headers = self._build_headers(ua, host_header=state.host_header)
                        stream = self._open_stream(
                            client,
                            state.url,
                            headers=headers,
                            sni_hostname=state.sni_hostname,
                        )
                        with stream as response:
                            response_host = state.host

                            if self._should_follow_redirect(response):
                                redirect_target = self._prepare_redirect_url(
                                    response,
                                    state.logical_url,
                                    state.redirect_count,
                                )
                                if redirect_target is None:
                                    raise RuntimeError("Redirect response missing Location header")
                                self._drain_sync_response_for_reuse(
                                    response,
                                    "Failed to read redirect response body",
                                )
                                (
                                    state.url,
                                    state.logical_url,
                                    state.host_header,
                                    state.sni_hostname,
                                    state.host,
                                ) = redirect_target
                                state.redirect_count += 1
                                continue

                            self._enforce_declared_response_size(response)

                            if self._is_redirect_response(response) and not self.follow_redirects:
                                content = self._read_sync_response_content(response)
                                elapsed_ms = (time.perf_counter() - start_time) * 1000
                                self._record_success(response_host)
                                self._remember_ssl_bypass_host(state.host)
                                return cast(
                                    FetchResult,
                                    self._make_fetch_result(
                                        content,
                                        state.requested_logical_url,
                                        state.logical_url,
                                        response,
                                        elapsed_ms,
                                        ua,
                                        ssl_attempt,
                                        ssl_bypass=True,
                                        total_attempt=total_attempt_num,
                                    ),
                                )

                            response.raise_for_status()
                            validate_content_type(response)

                            content = self._read_sync_response_content(response)

                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        self._remember_ssl_bypass_host(state.host)
                        return cast(
                            FetchResult,
                            self._make_fetch_result(
                                content,
                                state.requested_logical_url,
                                state.logical_url,
                                response,
                                elapsed_ms,
                                ua,
                                ssl_attempt,
                                ssl_bypass=True,
                                total_attempt=total_attempt_num,
                            ),
                        )

                except (UnsupportedContentTypeError, ResponseSizeLimitError):
                    raise

                except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
                    raise

                except httpx.HTTPStatusError as exc:
                    ssl_last_exc = exc
                    status_code = exc.response.status_code
                    retry_delay = retry_delay_seconds(exc.response, ssl_attempt)
                    response_host = state.host

                    if status_code not in RETRYABLE_STATUS:
                        raise
                    if ssl_attempt < remaining_attempts - 1:
                        logger.warning(
                            "SSL bypass attempt %d/%d failed with %d for %s, " "retrying in %.1fs",
                            ssl_attempt_num,
                            remaining_attempts,
                            status_code,
                            state.url,
                            retry_delay,
                        )
                        self._handle_retryable_status(response_host, status_code, retry_delay)
                        time.sleep(retry_delay)
                        ssl_attempt += 1
                        continue
                    self._handle_retryable_status(response_host, status_code, retry_delay)
                    raise

                except DomainCircuitOpenError:
                    raise

                except httpx.TooManyRedirects:
                    raise

                except Exception as exc:
                    ssl_last_exc = exc
                    self._record_failure(state.host)
                    if ssl_attempt < remaining_attempts - 1:
                        retry_delay = ssl_bypass_retry_delay(ssl_attempt)
                        logger.warning(
                            "SSL bypass attempt %d/%d failed for %s: %s, retrying in %.1fs",
                            ssl_attempt_num,
                            remaining_attempts,
                            state.url,
                            type(exc).__name__,
                            retry_delay,
                        )
                        time.sleep(retry_delay)
                        ssl_attempt += 1
                        continue
                    raise

        raise_ssl_bypass_exhausted(state.url, ssl_last_exc)
