"""
Link extraction and analysis from HTML documents
"""

from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from markdown_ingress.core.ssrf import normalize_hostname

HTTP_SCHEMES = {"http", "https"}
BLOCKED_URI_SCHEMES = ("javascript:", "mailto:", "tel:", "data:")
ResolvedLink = tuple[str, str]
CollectedLinks = tuple[list[str], list[str], list[str]]


def _resolve_effective_base_url(parser: HTMLParser, base_url: str) -> str:
    base_tag = parser.css_first("base[href]")
    if not base_tag:
        return base_url

    href = (base_tag.attributes.get("href") or "").strip()
    if not href:
        return base_url

    candidate = urljoin(base_url, href)
    parsed = urlparse(candidate)
    if parsed.scheme in HTTP_SCHEMES and parsed.hostname:
        return candidate
    return base_url


def _is_blocked_href(href: str) -> bool:
    scheme_lower = href.lstrip().lower()
    return scheme_lower.startswith(BLOCKED_URI_SCHEMES)


def _resolve_http_link(href: str, effective_base_url: str) -> ResolvedLink | None:
    absolute_url = urljoin(effective_base_url, href)
    parsed_url = urlparse(absolute_url)
    link_domain = normalize_hostname(parsed_url.hostname or "")
    if not link_domain or parsed_url.scheme not in HTTP_SCHEMES:
        return None
    return absolute_url, link_domain


def _collect_links(
    parser: HTMLParser,
    effective_base_url: str,
    base_domain: str,
) -> CollectedLinks:
    internal_links: list[str] = []
    external_links: list[str] = []
    anchor_links: list[str] = []

    for link in parser.css("a[href]"):
        href = (link.attributes.get("href") or "").strip()
        if not href:
            continue

        if href.startswith("#"):
            anchor_links.append(href)
            continue

        if _is_blocked_href(href):
            continue

        resolved = _resolve_http_link(href, effective_base_url)
        if resolved is None:
            continue

        absolute_url, link_domain = resolved
        if link_domain == base_domain:
            internal_links.append(absolute_url)
        else:
            external_links.append(absolute_url)

    return internal_links, external_links, anchor_links


def _deduplicate_links(links: list[str]) -> list[str]:
    return list(dict.fromkeys(links))


def _external_domain_counts(external_links: list[str]) -> dict[str, int]:
    domain_counts: dict[str, int] = {}
    for url in external_links:
        domain = normalize_hostname(urlparse(url).hostname or "")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    return domain_counts


class LinkAnalyzer:
    """Extract and analyze links from HTML documents"""

    def __init__(self):
        """Initialize link analyzer"""

    def analyze(self, html: str, base_url: str) -> dict[str, Any]:
        """
        Extract and classify all links from HTML.

        Args:
            html: HTML content
            base_url: Base URL for resolving relative links

        Returns:
            Dictionary with link analysis:
            {
                "total": int,
                "internal": List[str],
                "external": List[str],
                "anchors": List[str],
                "by_domain": Dict[str, int]
            }
        """
        parser = HTMLParser(html)

        # Parse base URL
        base_parsed = urlparse(base_url)
        base_domain = normalize_hostname(base_parsed.hostname or "")
        effective_base_url = _resolve_effective_base_url(parser, base_url)
        internal_links, external_links, anchor_links = _collect_links(
            parser,
            effective_base_url,
            base_domain,
        )

        # Deduplicate links while preserving insertion order
        internal_links = _deduplicate_links(internal_links)
        external_links = _deduplicate_links(external_links)
        anchor_links = _deduplicate_links(anchor_links)

        # Recompute domain counts from deduplicated external links so they
        # are consistent with the external_links list.
        domain_counts = _external_domain_counts(external_links)

        total = len(internal_links) + len(external_links) + len(anchor_links)

        return {
            "total": total,
            "internal": internal_links,
            "external": external_links,
            "anchors": anchor_links,
            "by_domain": domain_counts,
        }
