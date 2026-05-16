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


def raise_ssl_bypass_exhausted(url: str, last_exc: Exception | None) -> NoReturn:
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"SSL bypass fetch failed for {url} after {MAX_RETRIES} attempts")
