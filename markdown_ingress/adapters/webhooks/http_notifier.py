"""HTTP webhook notifier adapter with DNS pinning for SSRF protection."""

from __future__ import annotations

import json
import ipaddress
import socket
import ssl
import time
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlparse

import httpx

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


def _format_host_header(hostname: str, port: int, scheme: str) -> str:
    """Format the HTTP Host header, preserving IPv6 brackets and non-default ports."""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    default_port = 443 if scheme == "https" else 80
    return f"{host}:{port}" if port != default_port else host


def _validate_pinned_ip_for_ssrf(validated_ip: str, *, allow_local: bool = False) -> str:
    try:
        ip_obj = normalize_ip_for_ssrf(ipaddress.ip_address(validated_ip))
    except ValueError as exc:
        raise ValueError(f"validated_ip must be an IP address: {validated_ip!r}") from exc
    if not allow_local and is_blocked_ip_address(ip_obj):
        raise ValueError(f"validated_ip is blocked by SSRF protection: {ip_obj}")
    return str(ip_obj)


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
        self.max_retries = max(0, max_retries)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.timeout_seconds = max(1.0, timeout_seconds)
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
        parsed = urlparse(webhook_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid webhook URL port: {webhook_url!r}: {exc}") from exc
        if port is not None and port < 1:
            raise ValueError(
                f"Invalid webhook URL port: {webhook_url!r}. Port must be between 1 and 65535"
            )

        data = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None

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

        # If DNS pinning is requested, use low-level HTTP connection
        if validated_ip is not None:
            try:
                pinned_ip = _validate_pinned_ip_for_ssrf(
                    validated_ip,
                    allow_local=self.allow_local_webhooks,
                )
            except ValueError as exc:
                raise RuntimeError(
                    f"Webhook delivery blocked by SSRF protection for validated_ip: {exc}"
                ) from exc
            return self._notify_with_dns_pinning(webhook_url, data, pinned_ip)

        # Defense in depth: when the caller did NOT pre-validate the IP, apply
        # SSRF checks here so that direct use of the notifier (tests, plugins)
        # cannot be tricked into reaching internal networks via IP literals or
        # explicitly blocked hostnames.
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
            return self._notify_with_dns_pinning(webhook_url, data, validated_hostname)

        # Standard webhook delivery without DNS pinning
        for attempt in range(self.total_attempts):
            try:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    verify=True,
                ) as client:
                    response = client.post(
                        webhook_url,
                        content=data,
                        headers={"Content-Type": "application/json"},
                    )
                if 200 <= response.status_code < 300:
                    return
                last_error = RuntimeError(
                    f"Webhook delivery failed with status {response.status_code}"
                )
                # Retry on 429 (Too Many Requests) and 5xx server errors
                # Don't retry other 4xx client errors (they indicate configuration issues)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise last_error
                if attempt == self.total_attempts - 1:
                    break
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
            except _NON_RETRYABLE as exc:
                # Non-retryable: bad URL, bad payload, connection refused, etc.
                raise RuntimeError(
                    f"Webhook delivery failed (non-retryable) for {webhook_url}: {exc}"
                ) from exc
            except RuntimeError as exc:
                # Runtime fix (L7): only re-raise without wrapping when the
                # RuntimeError is the one we deliberately raised for a 4xx
                # non-retryable status (tracked via `last_error is exc`). Any
                # other RuntimeError (e.g. a transient connection-pool error
                # from httpx) joins the retry loop like a generic Exception so
                # the caller still gets the "after N attempts" wrapper.
                if last_error is exc:
                    raise
                last_error = exc
                if attempt == self.total_attempts - 1:
                    break
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
            except Exception as exc:
                last_error = exc
                if attempt == self.total_attempts - 1:
                    break
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
        raise RuntimeError(
            f"Webhook delivery failed after {self.total_attempts} attempts for {webhook_url}"
        ) from last_error

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
        parsed = urlparse(webhook_url)
        hostname = normalize_hostname(parsed.hostname or "")
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                f"Invalid webhook URL scheme: {parsed.scheme!r}. Only http and https are allowed."
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid webhook URL port: {webhook_url!r}: {exc}") from exc
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        elif port < 1:
            raise ValueError(
                f"Invalid webhook URL port: {webhook_url!r}. Port must be between 1 and 65535"
            )
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        # Use appropriate connection type based on scheme
        if parsed.scheme == "https":

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
                    super().__init__(
                        host, port=port, timeout=timeout, context=ssl.create_default_context()
                    )
                    self._validated_ip = validated_ip

                def connect(self) -> None:
                    source_address = getattr(self, "source_address", None)
                    context = getattr(self, "_context")
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

            def _make_connection() -> HTTPConnection:
                return _PinnedHTTPSConnection(
                    hostname,
                    validated_ip=validated_ip,
                    port=port,
                    timeout=self.timeout_seconds,
                )

        else:

            def _make_connection() -> HTTPConnection:
                return HTTPConnection(
                    validated_ip,
                    port=port,
                    timeout=self.timeout_seconds,
                )

        last_error: Exception | None = None
        for attempt in range(self.total_attempts):
            try:
                conn = _make_connection()
                try:
                    # Set Host header to original hostname
                    host_header = _format_host_header(hostname, port, parsed.scheme)
                    headers = {
                        "Content-Type": "application/json",
                        "Host": host_header,
                    }
                    conn.request("POST", path, body=data, headers=headers)
                    response = conn.getresponse()

                    # Handle response
                    if 200 <= response.status < 300:
                        return

                    # Handle error responses
                    if 400 <= response.status < 500 and response.status != 429:
                        # Client error - don't retry
                        raise RuntimeError(f"Webhook delivery failed with status {response.status}")

                    # Server error or 429 - retry
                    last_error = RuntimeError(
                        f"Webhook delivery failed with status {response.status}"
                    )
                    if attempt == self.total_attempts - 1:
                        break
                    if self.retry_delay_seconds > 0:
                        time.sleep(self.retry_delay_seconds)

                finally:
                    conn.close()

            except TimeoutError as exc:
                last_error = exc
                if attempt == self.total_attempts - 1:
                    break
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
            except OSError as exc:
                last_error = exc
                if attempt == self.total_attempts - 1:
                    break
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
            except RuntimeError:
                raise
            except Exception as exc:
                # Non-retryable errors
                raise RuntimeError(
                    f"Webhook delivery failed (non-retryable) for {webhook_url}: {exc}"
                ) from exc

        raise RuntimeError(
            f"Webhook delivery failed after {self.total_attempts} attempts for {webhook_url}"
        ) from last_error
