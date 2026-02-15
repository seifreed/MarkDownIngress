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
        base_domain = base_parsed.netloc.lower()

        internal_links = []
        external_links = []
        anchor_links = []
        domain_counts = {}

        # Find all <a> tags with href attribute
        links = parser.css("a[href]")

        for link in links:
            href = link.attributes.get("href", "").strip()
            if not href:
                continue

            # Classify link type
            if href.startswith("#"):
                # Pure anchor link
                anchor_links.append(href)
            elif (
                href.startswith("mailto:")
                or href.startswith("tel:")
                or href.startswith("javascript:")
            ):
                # Skip non-HTTP links
                continue
            else:
                # Resolve relative URLs
                absolute_url = urljoin(base_url, href)
                parsed_url = urlparse(absolute_url)
                link_domain = parsed_url.netloc.lower()

                # Skip invalid URLs
                if not link_domain or not parsed_url.scheme.startswith("http"):
                    continue

                # Classify as internal or external
                if link_domain == base_domain:
                    internal_links.append(absolute_url)
                else:
                    external_links.append(absolute_url)
                    # Count by domain
                    domain_counts[link_domain] = domain_counts.get(link_domain, 0) + 1

        # Deduplicate links
        internal_links = list(set(internal_links))
        external_links = list(set(external_links))
        anchor_links = list(set(anchor_links))

        total = len(internal_links) + len(external_links) + len(anchor_links)

        return {
            "total": total,
            "internal": internal_links,
            "external": external_links,
            "anchors": anchor_links,
            "by_domain": domain_counts,
        }
