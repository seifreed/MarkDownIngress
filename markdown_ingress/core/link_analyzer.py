"""
Link extraction and analysis from HTML documents
"""

from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser


class LinkAnalyzer:
    """Extract and analyze links from HTML documents"""

    def __init__(self):
        """Initialize link analyzer"""
        pass

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
        base_domain = (base_parsed.hostname or "").lower()

        internal_links: list[str] = []
        external_links: list[str] = []
        anchor_links: list[str] = []

        # Find all <a> tags with href attribute
        links = parser.css("a[href]")

        for link in links:
            href_attr = link.attributes.get("href") or ""
            href = href_attr.strip()
            if not href:
                continue

            # Classify link type
            if href.startswith("#"):
                # Pure anchor link
                anchor_links.append(href)
                continue

            # Security: Check for dangerous URI schemes
            # Strip leading whitespace and normalize for scheme check
            # to handle obfuscation like "  javascript:" or "\tjavascript:"
            stripped_href = href.lstrip()
            scheme_lower = stripped_href.lower()

            # Blocked schemes (including data: for XSS prevention)
            blocked_schemes = ("javascript:", "mailto:", "tel:", "data:")
            if any(scheme_lower.startswith(scheme) for scheme in blocked_schemes):
                # Skip non-HTTP/dangerous links
                continue

            # Resolve relative URLs
            absolute_url = urljoin(base_url, href)
            parsed_url = urlparse(absolute_url)
            link_domain = (parsed_url.hostname or "").lower()

            # Skip invalid URLs
            if not link_domain or not parsed_url.scheme.startswith("http"):
                continue

            # Classify as internal or external
            if link_domain == base_domain:
                internal_links.append(absolute_url)
            else:
                external_links.append(absolute_url)

        # Deduplicate links while preserving insertion order
        internal_links = list(dict.fromkeys(internal_links))
        external_links = list(dict.fromkeys(external_links))
        anchor_links = list(dict.fromkeys(anchor_links))

        # Recompute domain counts from deduplicated external links so they
        # are consistent with the external_links list.
        domain_counts = {}
        for url in external_links:
            domain = (urlparse(url).hostname or "").lower()
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

        total = len(internal_links) + len(external_links) + len(anchor_links)

        return {
            "total": total,
            "internal": internal_links,
            "external": external_links,
            "anchors": anchor_links,
            "by_domain": domain_counts,
        }
