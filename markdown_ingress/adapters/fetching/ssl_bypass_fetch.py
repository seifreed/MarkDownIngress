"""SSL bypass fallback fetch path for the HTTPX fetcher."""

from __future__ import annotations

from markdown_ingress.adapters.fetching.ssl_bypass_async import AsyncSslBypassFetchMixin
from markdown_ingress.adapters.fetching.ssl_bypass_state import SslBypassFetchState
from markdown_ingress.adapters.fetching.ssl_bypass_sync import SyncSslBypassFetchMixin


class SslBypassFetchMixin(AsyncSslBypassFetchMixin, SyncSslBypassFetchMixin):
    """Certificate-verification bypass retry path for fetch operations."""


__all__ = ["SslBypassFetchMixin", "SslBypassFetchState"]
