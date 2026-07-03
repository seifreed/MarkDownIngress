"""Async SSL-bypass fetch retry path."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import httpx

from markdown_ingress.adapters.fetching.fetch_shared import (
    FetchAttemptSharedMixin,
    SslBypassSharedMixin,
)
from markdown_ingress.adapters.fetching.http_support import (
    FETCH_ATTEMPT_ERRORS,
    MAX_RETRIES,
    RETRYABLE_STATUS,
    ResponseSizeLimitError,
    retry_delay_seconds,
    ssl_bypass_retry_delay,
    validate_content_type,
)
from markdown_ingress.adapters.fetching.ssl_bypass_state import (
    SslBypassAttemptContext,
    SslBypassFetchState,
    raise_ssl_bypass_exhausted,
)
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)


class AsyncSslBypassFetchMixin(SslBypassSharedMixin, FetchAttemptSharedMixin):
    """Certificate-verification bypass retry path for async fetch operations."""

    async def _fetch_with_ssl_bypass(
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
            async with self._create_async_ssl_bypass_client() as client:
                try:
                    result = await self._run_async_ssl_bypass_attempt(
                        client,
                        state,
                        ssl_attempt=ssl_attempt,
                        total_attempt=consumed_attempts + ssl_attempt + 1,
                    )
                    return cast(FetchResult, result)
                except (UnsupportedContentTypeError, ResponseSizeLimitError):
                    raise
                except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
                    raise
                except httpx.HTTPStatusError as exc:
                    ssl_last_exc = exc
                    await self._handle_async_ssl_bypass_status_error(
                        state, exc, ssl_attempt, remaining_attempts
                    )
                except (DomainCircuitOpenError, httpx.TooManyRedirects):
                    raise
                except FETCH_ATTEMPT_ERRORS as exc:
                    ssl_last_exc = exc
                    await self._handle_async_ssl_bypass_generic_error(
                        state, exc, ssl_attempt, remaining_attempts
                    )
            ssl_attempt += 1

        return raise_ssl_bypass_exhausted(state.url, ssl_last_exc)

    def _create_async_ssl_bypass_client(self: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            max_redirects=self.max_redirects,
            verify=self._build_ssl_context(verify_certificates=False),
            trust_env=False,
        )

    async def _run_async_ssl_bypass_attempt(
        self: Any,
        client: Any,
        state: SslBypassFetchState,
        *,
        ssl_attempt: int,
        total_attempt: int,
    ) -> FetchResult:
        while True:
            ua, start_time = await self._prepare_async_attempt(state)
            attempt = SslBypassAttemptContext(
                state=state,
                start_time=start_time,
                user_agent=ua,
                ssl_attempt=ssl_attempt,
                total_attempt=total_attempt,
            )
            headers = self._build_headers(ua, host_header=state.host_header)
            stream = self._open_stream(
                client,
                state.url,
                headers=headers,
                sni_hostname=state.sni_hostname,
            )
            async with stream as response:
                result = await self._handle_async_ssl_bypass_response(
                    response,
                    attempt,
                )
                if result is not None:
                    return cast(FetchResult, result)

    async def _handle_async_ssl_bypass_response(
        self: Any,
        response: Any,
        attempt: SslBypassAttemptContext,
    ) -> FetchResult | None:
        state = attempt.state
        if self._should_follow_redirect(response):
            await self._follow_async_redirect(response, state)
            return None
        self._enforce_declared_response_size(response)

        if self._is_redirect_response(response) and not self.follow_redirects:
            content = await self._read_async_response_content(response)
            return cast(
                FetchResult,
                self._finish_ssl_bypass_result(
                    response,
                    attempt,
                    content,
                ),
            )

        response.raise_for_status()
        validate_content_type(response)
        content = await self._read_async_response_content(response)
        return cast(
            FetchResult,
            self._finish_ssl_bypass_result(
                response,
                attempt,
                content,
            ),
        )

    async def _handle_async_ssl_bypass_status_error(
        self: Any,
        state: SslBypassFetchState,
        exc: httpx.HTTPStatusError,
        ssl_attempt: int,
        remaining_attempts: int,
    ) -> None:
        status_code = exc.response.status_code
        retry_delay = retry_delay_seconds(exc.response, ssl_attempt)
        if status_code not in RETRYABLE_STATUS:
            raise exc

        response_host = state.host
        self._handle_retryable_status(response_host, status_code, retry_delay)
        if ssl_attempt >= remaining_attempts - 1:
            raise exc

        await self._sleep_before_async_ssl_bypass_status_retry(
            state,
            ssl_attempt,
            remaining_attempts,
            status_code,
            retry_delay,
        )

    async def _sleep_before_async_ssl_bypass_status_retry(
        self: Any,
        state: SslBypassFetchState,
        ssl_attempt: int,
        remaining_attempts: int,
        status_code: int,
        retry_delay: float,
    ) -> None:
        self._log_ssl_bypass_retry(
            state,
            ssl_attempt,
            remaining_attempts,
            retry_delay,
            status_code=status_code,
        )
        await asyncio.sleep(retry_delay)

    async def _handle_async_ssl_bypass_generic_error(
        self: Any,
        state: SslBypassFetchState,
        exc: Exception,
        ssl_attempt: int,
        remaining_attempts: int,
    ) -> None:
        self._record_failure(state.host)
        if ssl_attempt >= remaining_attempts - 1:
            raise exc

        retry_delay = ssl_bypass_retry_delay(ssl_attempt)
        self._log_ssl_bypass_retry(
            state,
            ssl_attempt,
            remaining_attempts,
            retry_delay,
            exc=exc,
        )
        await asyncio.sleep(retry_delay)
