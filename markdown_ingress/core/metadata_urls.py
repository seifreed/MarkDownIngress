"""URL helpers for metadata extraction."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser


def extract_canonical_url(parser: HTMLParser, url: str) -> str | None:
    """Extract a validated canonical URL from link/meta tags."""
    base_url = _resolve_base_url(parser, url)

    for canonical_link in _iter_links_with_rel_token(parser, "canonical"):
        href = (canonical_link.attributes.get("href") or "").strip()
        if href:
            resolved = _resolve_candidate_url(href, base_url)
            if resolved is not None:
                return resolved

    og_url = parser.css_first('meta[property="og:url"]')
    if og_url:
        content = (og_url.attributes.get("content") or "").strip()
        if content:
            resolved = _resolve_candidate_url(content, base_url)
            if resolved is not None:
                return resolved

    return url


def _validate_http_url(candidate: str) -> str | None:
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _resolve_base_url(parser: HTMLParser, url: str) -> str:
    base_tag = parser.css_first("base[href]")
    if not base_tag:
        return url
    href = (base_tag.attributes.get("href") or "").strip()
    if not href:
        return url
    return _validate_http_url(urljoin(url, href)) or url


def _resolve_candidate_url(candidate: str, base_url: str) -> str | None:
    return _validate_http_url(urljoin(base_url, candidate))


def _iter_links_with_rel_token(parser: HTMLParser, token: str) -> Iterator[Any]:
    normalized_token = token.lower()
    for link in parser.css("link[rel]"):
        rel = link.attributes.get("rel") or ""
        rel_tokens = {part.lower() for part in rel.split() if part}
        if normalized_token in rel_tokens:
            yield link
