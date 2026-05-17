"""Sync HTTPX fetch path."""

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
    should_retry_with_ssl_bypass,
    validate_content_type,
)
from markdown_ingress.adapters.fetching.ssl_bypass_fetch import SslBypassFetchState
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)


class SyncHttpxFetchMixin:
    """Sync HTTPX fetch implementation."""

    def fetch_sync(self: Any, url: str) -> FetchResult:
        url, logical_url, host_header, sni_hostname, host = (
            self._prepare_request_url_with_dns_retry(url)
        )
        requested_logical_url = logical_url

        last_exc: Exception | None = None
        ssl_retried = self._is_ssl_bypass_active(host)
        verify: bool | str = not ssl_retried
        attempt = 0
        redirect_count = 0
        previous_ua: str | None = None

        client = self._get_sync_client()
        while attempt < MAX_RETRIES and not ssl_retried:
            ua = self._next_user_agent(previous=previous_ua)
            previous_ua = ua
            self._ensure_circuit_closed(host)
            sleep_for = self._reserve_domain_slot(host)
            if sleep_for > 0:
                time.sleep(sleep_for)
                self._ensure_circuit_closed(host)
            start_time = time.perf_counter()

            try:
                headers = self._build_headers(ua, host_header=host_header)
                stream = self._open_stream(client, url, headers=headers, sni_hostname=sni_hostname)
                with stream as response:
                    response_host = host
                    if self._should_follow_redirect(response):
                        redirect_target = self._prepare_redirect_url(
                            response, logical_url, redirect_count
                        )
                        if redirect_target is None:
                            raise RuntimeError("Redirect response missing Location header")
                        self._drain_sync_response_for_reuse(
                            response, "Failed to read redirect response body"
                        )
                        url, logical_url, host_header, sni_hostname, host = redirect_target
                        redirect_count += 1
                        continue

                    self._enforce_declared_response_size(response)

                    if self._is_redirect_response(response) and not self.follow_redirects:
                        content = self._read_sync_response_content(response)
                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        return cast(
                            FetchResult,
                            self._make_fetch_result(
                                content,
                                requested_logical_url,
                                logical_url,
                                response,
                                elapsed_ms,
                                ua,
                                attempt,
                            ),
                        )

                    if response.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES - 1:
                        retry_delay = retry_delay_seconds(response, attempt)
                        self._handle_retryable_status(
                            response_host, response.status_code, retry_delay
                        )
                        self._drain_sync_response_for_reuse(
                            response, "Failed to read response body during retry"
                        )
                        time.sleep(retry_delay)
                        attempt += 1
                        continue

                    response.raise_for_status()
                    validate_content_type(response)

                    content = self._read_sync_response_content(response)

                elapsed_ms = (time.perf_counter() - start_time) * 1000
                self._record_success(response_host)
                return cast(
                    FetchResult,
                    self._make_fetch_result(
                        content,
                        requested_logical_url,
                        logical_url,
                        response,
                        elapsed_ms,
                        ua,
                        attempt,
                    ),
                )

            except (UnsupportedContentTypeError, ResponseSizeLimitError):
                raise

            except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
                raise

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                response_host = host
                if status_code not in RETRYABLE_STATUS:
                    raise
                retry_delay = retry_delay_seconds(exc.response, attempt)
                self._handle_retryable_status(response_host, status_code, retry_delay)
                if attempt >= MAX_RETRIES - 1:
                    raise
                time.sleep(retry_delay)
                attempt += 1
                continue

            except DomainCircuitOpenError:
                raise

            except httpx.TooManyRedirects:
                raise

            except Exception as exc:
                last_exc = exc
                self._record_failure(host)

                if should_retry_with_ssl_bypass(
                    allow_ssl_bypass=self.allow_ssl_bypass,
                    ssl_retried=ssl_retried,
                    exc=exc,
                ):
                    logger.warning(
                        "SSL verification failed for %s, retrying with certificate verification "
                        "disabled. "
                        "This bypass is insecure and should only be used for testing.",
                        url,
                    )
                    verify = False  # pragma: no cover
                    ssl_retried = True  # pragma: no cover
                    attempt += 1
                    break  # pragma: no cover
                attempt += 1

        if ssl_retried and verify is False:
            return cast(
                FetchResult,
                self._fetch_sync_with_ssl_bypass(
                    SslBypassFetchState(
                        url=url,
                        logical_url=logical_url,
                        requested_logical_url=requested_logical_url,
                        host_header=host_header,
                        sni_hostname=sni_hostname,
                        host=host,
                        redirect_count=redirect_count,
                        previous_ua=previous_ua,
                    ),
                    consumed_attempts=attempt,
                    last_exc=last_exc,
                ),
            )

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Fetch failed for {url} after {MAX_RETRIES} attempts")
