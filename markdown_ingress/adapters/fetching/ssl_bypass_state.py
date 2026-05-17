"""Shared SSL-bypass fetch state and failure helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from markdown_ingress.adapters.fetching.http_support import MAX_RETRIES


@dataclass
class SslBypassFetchState:
    """Mutable state carried through SSL-bypass redirects and retries."""

    url: str
    logical_url: str
    requested_logical_url: str
    host_header: str | None
    sni_hostname: str | None
    host: str
    redirect_count: int
    previous_ua: str | None

    def update_redirect_target(
        self,
        redirect_target: tuple[str, str, str | None, str | None, str],
    ) -> None:
        self.url, self.logical_url, self.host_header, self.sni_hostname, self.host = redirect_target
        self.redirect_count += 1


@dataclass(frozen=True)
class SslBypassAttemptContext:
    """Immutable timing and retry metadata for one SSL-bypass fetch attempt."""

    state: SslBypassFetchState
    start_time: float
    user_agent: str
    ssl_attempt: int
    total_attempt: int


def raise_ssl_bypass_exhausted(url: str, last_exc: Exception | None) -> NoReturn:
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"SSL bypass fetch failed for {url} after {MAX_RETRIES} attempts")
