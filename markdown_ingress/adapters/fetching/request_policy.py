"""Request preparation, redirect, SSL, and header policy for the HTTPX fetcher."""

from __future__ import annotations

import random
import socket
import ssl
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from markdown_ingress.adapters.fetching.http_support import (
    FOLLOW_REDIRECT_STATUS,
    SAFE_HEADERS,
    PreparedRequest,
    format_host_header,
)
from markdown_ingress.core.ssrf import (
    normalize_hostname,
    resolve_allow_local_urls,
    validate_http_url_no_ssrf,
)

_rng = random.SystemRandom()


def _choose_user_agent(candidates: tuple[str, ...] | list[str]) -> str:
    return str(_rng.choice(candidates))


class FetchRequestPolicyMixin:
    """URL validation, redirect handling, SSL context, and request headers."""

    _fixed_ua: str | None
    _stable_ua: str | None
    _last_rotating_ua: str | None

    def _is_ssl_bypass_active(self: Any, host: str) -> bool:
        with self._ssl_bypass_lock:
            expiry = cast(float | None, self._ssl_bypass_hosts.get(host))
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                self._ssl_bypass_hosts.pop(host, None)
                return False
            return True

    def _should_follow_redirect(self: Any, response: httpx.Response) -> bool:
        if not self.follow_redirects:
            return False
        has_redirect_location = getattr(response, "has_redirect_location", None)
        if has_redirect_location is not None:
            return bool(has_redirect_location)
        return response.status_code in FOLLOW_REDIRECT_STATUS and bool(
            response.headers.get("location")
        )

    def _is_redirect_response(self: Any, response: httpx.Response) -> bool:
        return (
            bool(getattr(response, "is_redirect", False))
            or response.status_code in FOLLOW_REDIRECT_STATUS
        )

    def _prepare_request_url(self: Any, url: str) -> PreparedRequest:
        logical_url = str(url).strip()
        validated_url = str(self._validate_url(logical_url, allow_local_urls=self.allow_local_urls))
        original_parts = urlsplit(logical_url)
        original_hostname = original_parts.hostname or ""
        validated_hostname = urlsplit(validated_url).hostname or ""
        host_header: str | None = None
        sni_hostname: str | None = None
        if original_hostname and original_hostname != validated_hostname:
            host_header = format_host_header(
                original_hostname,
                original_parts.port,
                original_parts.scheme,
            )
            sni_hostname = original_hostname
        logical_host = str(self._host_key(logical_url))
        return PreparedRequest(
            validated_url,
            logical_url,
            host_header,
            sni_hostname,
            logical_host,
        )

    def _prepare_redirect_url(
        self: Any,
        response: httpx.Response,
        logical_url: str,
        redirect_count: int,
    ) -> PreparedRequest | None:
        location = response.headers.get("location")
        if not location:
            return None
        if redirect_count >= self.max_redirects:
            raise httpx.TooManyRedirects(
                f"Exceeded maximum allowed redirects: {self.max_redirects}",
                request=getattr(response, "request", None),
            )
        redirect_url = str(httpx.URL(logical_url).join(location))
        return cast(PreparedRequest, self._prepare_request_url(redirect_url))

    def _build_ssl_context(self: Any, *, verify_certificates: bool = True) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not verify_certificates:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        ca_bundle = cast(str | None, self.ca_bundle)
        if ca_bundle:
            if Path(ca_bundle).is_dir():
                ctx.load_verify_locations(capath=ca_bundle)
            else:
                ctx.load_verify_locations(cafile=ca_bundle)
        return ctx

    @property
    def user_agent(self: Any) -> str:
        fixed_ua = cast(str | None, self._fixed_ua)
        if fixed_ua is not None:
            return fixed_ua
        if not self.rotate_ua:
            if self._stable_ua is None:
                self._stable_ua = _choose_user_agent(cast(tuple[str, ...], self._ua_pool))
            return cast(str, self._stable_ua)
        return FetchRequestPolicyMixin._next_user_agent(
            self, previous=cast(str | None, self._last_rotating_ua)
        )

    def _next_user_agent(self: Any, *, previous: str | None = None) -> str:
        fixed_ua = cast(str | None, self._fixed_ua)
        if fixed_ua is not None:
            return fixed_ua
        if not self.rotate_ua:
            stable_ua = cast(str | None, self._stable_ua)
            if stable_ua is None:
                stable_ua = _choose_user_agent(cast(tuple[str, ...], self._ua_pool))
                self._stable_ua = stable_ua
            return stable_ua

        ua_pool = cast(tuple[str, ...], self._ua_pool)
        if previous is not None and len(ua_pool) > 1:
            candidates = [ua for ua in ua_pool if ua != previous]
        else:
            candidates = list(ua_pool)
        ua = _choose_user_agent(candidates)
        self._last_rotating_ua = ua
        return ua

    def _build_headers(self: Any, ua: str, *, host_header: str | None = None) -> dict:
        headers = dict(SAFE_HEADERS)
        headers["User-Agent"] = ua
        if host_header is not None:
            headers["Host"] = host_header
        return headers

    @staticmethod
    def _stream_extensions(sni_hostname: str | None) -> dict[str, Any] | None:
        if sni_hostname is None:
            return None
        return {"sni_hostname": sni_hostname.encode("ascii")}

    @staticmethod
    def _resolve_allow_local_urls(allow_local_urls: bool | None) -> bool:
        return resolve_allow_local_urls(allow_local_urls)

    @staticmethod
    def _is_dns_transient_error(exc: Exception) -> bool:
        """Return True when the exception indicates a transient DNS failure."""
        if isinstance(exc, socket.gaierror):
            return True
        if isinstance(exc, ValueError):
            msg = str(exc).lower()
            for hint in ("dns", "resolve", "nxdomain", "timeout", "temporary failure"):
                if hint in msg:
                    return True
        return False

    @staticmethod
    def _validate_url(url: str, *, allow_local_urls: bool = False) -> str:
        return validate_http_url_no_ssrf(
            url,
            allow_local=allow_local_urls,
            resolve_dns=True,
        )

    @staticmethod
    def _host_key(url: str) -> str:
        return normalize_hostname(urlsplit(url).hostname or "")

    @classmethod
    def _effective_host(cls, final_url: str | None, fallback_host: str) -> str:
        if not final_url:
            return fallback_host
        host_key = cast(Callable[[str], str], cls._host_key)
        resolved = host_key(final_url)
        return resolved or fallback_host

    def _handle_retryable_status(
        self: Any, response_host: str, status_code: int, retry_delay: float
    ) -> None:
        if status_code in {403, 429}:
            self._record_soft_throttle(response_host, retry_delay)
        else:
            self._defer_host(response_host, retry_delay)
            self._record_failure(response_host)
