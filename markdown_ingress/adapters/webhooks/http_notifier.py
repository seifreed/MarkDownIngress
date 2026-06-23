"""HTTP webhook notifier adapter with DNS pinning for SSRF protection."""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from markdown_ingress.adapters.fetching.http_support import format_host_header, hostname_to_ascii
from markdown_ingress.config_validation import ensure_finite_float, ensure_int
from markdown_ingress.core.ssrf import (
    is_blocked_ip_address,
    normalize_hostname,
    normalize_ip_for_ssrf,
    validate_http_url_no_ssrf,
)

# Errors that should NOT be retried (client-side or configuration problems).
# Note: URLError is intentionally NOT included - it includes transient network errors
# like DNS failures, connection refused, and timeouts which SHOULD be retried.
_NON_RETRYABLE = (ValueError, TypeError)


@dataclass(frozen=True)
class _WebhookAttemptOutcome:
    delivered: bool = False
    error: Exception | None = None
    raise_now: RuntimeError | None = None


@dataclass(frozen=True)
class _PinnedWebhookTarget:
    scheme: str
    hostname: str
    port: int
    path: str


class _PinnedHTTPSConnection(HTTPSConnection):
    """HTTPS connection that pins transport to a validated IP but keeps hostname SNI."""

    def __init__(
        self,
        host: str,
        *,
        validated_ip: str,
        port: int | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._validated_ip = validated_ip

    def connect(self) -> None:
        source_address = getattr(self, "source_address", None)
        context = cast(Any, self)._context
        raw_sock = socket.create_connection(
            (self._validated_ip, self.port),
            self.timeout,
            source_address,
        )
        try:
            self.sock = context.wrap_socket(raw_sock, server_hostname=self.host)
        except Exception:
            raw_sock.close()
            raise


def _validate_non_negative_int(field_name: str, value: object) -> int:
    value = ensure_int(field_name, value)
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def _validate_finite_float(field_name: str, value: object, *, minimum: float) -> float:
    numeric = ensure_finite_float(field_name, value)
    if numeric < minimum:
        raise ValueError(f"{field_name} must be >= {minimum:g}")
    return numeric


def _validate_pinned_ip_for_ssrf(validated_ip: str, *, allow_local: bool = False) -> str:
    try:
        ip_obj = normalize_ip_for_ssrf(ipaddress.ip_address(validated_ip))
    except ValueError as exc:
        raise ValueError(f"validated_ip must be an IP address: {validated_ip!r}") from exc
    if not allow_local and is_blocked_ip_address(ip_obj):
        raise ValueError(f"validated_ip is blocked by SSRF protection: {ip_obj}")
    return str(ip_obj)


def _parse_webhook_url(webhook_url: str):
    parsed = urlparse(webhook_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid webhook URL port: {webhook_url!r}: {exc}") from exc
    if port is not None and port < 1:
        raise ValueError(
            f"Invalid webhook URL port: {webhook_url!r}. Port must be between 1 and 65535"
        )
    return parsed


def _parse_pinned_webhook_target(webhook_url: str) -> _PinnedWebhookTarget:
    parsed = _parse_webhook_url(webhook_url)
    hostname = hostname_to_ascii(normalize_hostname(parsed.hostname or ""))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"Invalid webhook URL scheme: {parsed.scheme!r}. Only http and https are allowed."
        )
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return _PinnedWebhookTarget(parsed.scheme, hostname, port, path)


def _response_status_outcome(status_code: int) -> _WebhookAttemptOutcome:
    if 200 <= status_code < 300:
        return _WebhookAttemptOutcome(delivered=True)
    error = RuntimeError(f"Webhook delivery failed with status {status_code}")
    if 400 <= status_code < 500 and status_code != 429:
        return _WebhookAttemptOutcome(raise_now=error)
    return _WebhookAttemptOutcome(error=error)


def _raise_delivery_failed_after_attempts(
    webhook_url: str, total_attempts: int, last_error: Exception | None
) -> None:
    raise RuntimeError(
        f"Webhook delivery failed after {total_attempts} attempts for {webhook_url}"
    ) from last_error


class HTTPWebhookNotifier:
    """Deliver JSON webhook payloads with bounded retries.

    Supports DNS pinning to prevent DNS rebinding attacks. When validated_ip
    is provided, the connection is made directly to that IP with the Host
    header set to the original hostname. ``max_retries`` counts retries after
    the initial delivery attempt.
    """

    def __init__(
        self,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.25,
        timeout_seconds: float = 5.0,
        allow_local_webhooks: bool = False,
    ) -> None:
        self.max_retries = _validate_non_negative_int("max_retries", max_retries)
        self.retry_delay_seconds = _validate_finite_float(
            "retry_delay_seconds", retry_delay_seconds, minimum=0.0
        )
        self.timeout_seconds = _validate_finite_float(
            "timeout_seconds", timeout_seconds, minimum=1.0
        )
        # Security fix (S2): when the caller did not pre-validate the IP, the
        # notifier applies its own SSRF check. This flag preserves the queue's
        # `allow_local_webhooks` behaviour so legitimate localhost webhooks
        # (tests, internal integrations) still work.
        self.allow_local_webhooks = allow_local_webhooks

    @property
    def total_attempts(self) -> int:
        """Total delivery attempts (1 initial try + configured retries)."""
        return self.max_retries + 1

    def notify(
        self,
        webhook_url: str,
        payload: dict[str, object],
        *,
        validated_ip: str | None = None,
    ) -> None:
        """Deliver webhook payload with optional DNS pinning.

        Args:
            webhook_url: Target URL for webhook delivery
            payload: JSON payload to deliver
            validated_ip: Optional pre-validated IP address for DNS pinning.
                         When provided, connects to this IP directly while using
                 the original hostname for the Host header.

        Raises:
            RuntimeError: If webhook delivery fails after all retries
        """
        parsed = _parse_webhook_url(webhook_url)
        data = json.dumps(payload).encode("utf-8")

        self._validate_webhook_url_without_dns_resolution(webhook_url)
        pinned_ip = self._pinned_delivery_ip(webhook_url, parsed, validated_ip)
        if pinned_ip is not None:
            self._notify_with_dns_pinning(webhook_url, data, pinned_ip)
            return
        self._notify_without_dns_pinning(webhook_url, data)

    def _validate_webhook_url_without_dns_resolution(self, webhook_url: str) -> None:
        try:
            validate_http_url_no_ssrf(
                webhook_url,
                allow_local=self.allow_local_webhooks,
                resolve_dns=False,
            )
        except ValueError as exc:
            raise RuntimeError(
                f"Webhook delivery blocked by SSRF protection for {webhook_url}: {exc}"
            ) from exc

    def _pinned_delivery_ip(
        self,
        webhook_url: str,
        parsed: Any,
        validated_ip: str | None,
    ) -> str | None:
        if validated_ip is not None:
            try:
                return _validate_pinned_ip_for_ssrf(
                    validated_ip,
                    allow_local=self.allow_local_webhooks,
                )
            except ValueError as exc:
                raise RuntimeError(
                    f"Webhook delivery blocked by SSRF protection for validated_ip: {exc}"
                ) from exc

        return self._validated_hostname_rewrite_ip(webhook_url, parsed)

    def _validated_hostname_rewrite_ip(self, webhook_url: str, parsed: Any) -> str | None:
        try:
            validated_url = validate_http_url_no_ssrf(
                webhook_url,
                allow_local=self.allow_local_webhooks,
                resolve_dns=True,
            )
        except ValueError as exc:
            raise RuntimeError(
                f"Webhook delivery blocked by SSRF protection for {webhook_url}: {exc}"
            ) from exc
        validated_hostname = normalize_hostname(urlparse(validated_url).hostname or "")
        original_hostname = normalize_hostname(parsed.hostname or "")
        if validated_hostname and validated_hostname != original_hostname:
            return validated_hostname
        return None

    def _sleep_before_retry(self) -> None:
        if self.retry_delay_seconds > 0:
            time.sleep(self.retry_delay_seconds)

    def _post_without_dns_pinning(self, webhook_url: str, data: bytes) -> httpx.Response:
        with httpx.Client(
            timeout=self.timeout_seconds,
            verify=True,
        ) as client:
            return client.post(
                webhook_url,
                content=data,
                headers={"Content-Type": "application/json"},
            )

    def _standard_delivery_attempt(self, webhook_url: str, data: bytes) -> _WebhookAttemptOutcome:
        try:
            response = self._post_without_dns_pinning(webhook_url, data)
            return _response_status_outcome(response.status_code)
        except _NON_RETRYABLE as exc:
            raise RuntimeError(
                f"Webhook delivery failed (non-retryable) for {webhook_url}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - webhook transport errors are retryable
            return _WebhookAttemptOutcome(error=exc)

    def _run_delivery_loop(
        self,
        webhook_url: str,
        make_attempt: Callable[[], _WebhookAttemptOutcome],
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(self.total_attempts):
            outcome = make_attempt()
            if outcome.delivered:
                return
            if outcome.raise_now is not None:
                raise outcome.raise_now
            last_error = outcome.error
            if attempt == self.total_attempts - 1:
                break
            self._sleep_before_retry()

        _raise_delivery_failed_after_attempts(webhook_url, self.total_attempts, last_error)

    def _notify_without_dns_pinning(self, webhook_url: str, data: bytes) -> None:
        self._run_delivery_loop(
            webhook_url,
            lambda: self._standard_delivery_attempt(webhook_url, data),
        )

    def _notify_with_dns_pinning(
        self,
        webhook_url: str,
        data: bytes,
        validated_ip: str,
    ) -> None:
        """Deliver webhook using DNS pinning to prevent DNS rebinding.

        Connects directly to the validated IP address while using the original
        hostname for the Host header, ensuring the DNS resolution used at
        validation time is the same as the connection target.

        Args:
            webhook_url: Target URL (used for hostname and path)
            data: JSON payload bytes
            validated_ip: Pre-validated IP address to connect to

        Raises:
            RuntimeError: If delivery fails
        """
        target = _parse_pinned_webhook_target(webhook_url)
        self._run_delivery_loop(
            webhook_url,
            lambda: self._pinned_delivery_attempt(webhook_url, data, target, validated_ip),
        )

    def _make_pinned_connection(
        self, target: _PinnedWebhookTarget, validated_ip: str
    ) -> HTTPConnection:
        if target.scheme == "https":
            return _PinnedHTTPSConnection(
                target.hostname,
                validated_ip=validated_ip,
                port=target.port,
                timeout=self.timeout_seconds,
            )
        return HTTPConnection(
            validated_ip,
            port=target.port,
            timeout=self.timeout_seconds,
        )

    def _send_pinned_request(
        self, conn: HTTPConnection, target: _PinnedWebhookTarget, data: bytes
    ) -> int:
        host_header = format_host_header(target.hostname, target.port, target.scheme)
        headers = {
            "Content-Type": "application/json",
            "Host": host_header,
        }
        conn.request("POST", target.path, body=data, headers=headers)
        response = conn.getresponse()
        return response.status

    def _pinned_delivery_attempt(
        self,
        webhook_url: str,
        data: bytes,
        target: _PinnedWebhookTarget,
        validated_ip: str,
    ) -> _WebhookAttemptOutcome:
        try:
            conn = self._make_pinned_connection(target, validated_ip)
            try:
                status = self._send_pinned_request(conn, target, data)
            finally:
                conn.close()
            return _response_status_outcome(status)
        except (TimeoutError, OSError) as exc:
            return _WebhookAttemptOutcome(error=exc)
        except RuntimeError as exc:
            return _WebhookAttemptOutcome(raise_now=exc)
        except Exception as exc:
            raise RuntimeError(
                f"Webhook delivery failed (non-retryable) for {webhook_url}: {exc}"
            ) from exc
