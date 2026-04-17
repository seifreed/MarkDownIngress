"""Moved to adapters/fetching/httpx_fetcher.py — re-exported for backward compatibility."""
from markdown_ingress.adapters.fetching.httpx_fetcher import (
    DomainCircuitOpenError,
    Fetcher,
    UnsupportedContentTypeError,
)

__all__ = ["DomainCircuitOpenError", "Fetcher", "UnsupportedContentTypeError"]
