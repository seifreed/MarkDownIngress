"""
Metadata extraction from HTML documents
"""

from typing import Any

from selectolax.parser import HTMLParser

from markdown_ingress.core.metadata_basic import (
    detect_content_type,
    extract_keywords,
    first_meta_content,
)
from markdown_ingress.core.metadata_jsonld import (
    parse_author_from_jsonld_script,
    parse_date_from_jsonld_script,
)
from markdown_ingress.core.metadata_language import (
    LANGDETECT_SAMPLE_CHARS,
    detect_content_language_info,
    empty_language_info,
    extract_declared_language_info,
)
from markdown_ingress.core.metadata_urls import extract_canonical_url

_LANGDETECT_SAMPLE_CHARS = LANGDETECT_SAMPLE_CHARS


class MetadataExtractor:
    """Extract rich metadata from HTML documents"""

    def extract(
        self,
        html: str,
        url: str,
        *,
        detect_language: bool = True,
        normalize_multilingual: bool = True,
    ) -> dict[str, Any]:
        """
        Extract comprehensive metadata from HTML.

        Args:
            html: HTML content
            url: URL of the document

        Returns:
            Dictionary with extracted metadata fields
        """
        parser = HTMLParser(html)

        language_info = self._extract_language_info(
            parser,
            detect_language=detect_language,
            normalize_multilingual=normalize_multilingual,
        )
        return {
            "author": self._extract_author(parser),
            "published_date": self._extract_published_date(parser),
            "modified_date": self._extract_modified_date(parser),
            "language": language_info["language"],
            "language_source": language_info["source"],
            "language_confidence": language_info["confidence"],
            "description": self._extract_description(parser),
            "keywords": self._extract_keywords(parser),
            "canonical_url": extract_canonical_url(parser, url),
            "site_name": self._extract_site_name(parser),
            "content_type": self._detect_content_type(parser),
        }

    def _extract_author(self, parser: HTMLParser) -> str | None:
        """Extract author from meta tags or schema.org"""
        return first_meta_content(
            parser,
            ('meta[name="author"]', 'meta[property="article:author"]'),
        ) or self._extract_author_from_jsonld(parser)

    def _extract_author_from_jsonld(self, parser: HTMLParser) -> str | None:
        """Extract author from schema.org JSON-LD scripts"""
        scripts = parser.css('script[type="application/ld+json"]')
        for script in scripts:
            author = parse_author_from_jsonld_script(script)
            if author:
                return author
        return None

    def _extract_meta_date(
        self, parser: HTMLParser, selectors: tuple[str, ...], jsonld_field: str
    ) -> str | None:
        """Return the first non-empty meta content, falling back to schema.org JSON-LD."""
        return first_meta_content(parser, selectors) or self._extract_date_from_jsonld(
            parser, jsonld_field
        )

    def _extract_published_date(self, parser: HTMLParser) -> str | None:
        """Extract published date from meta tags or schema.org"""
        return self._extract_meta_date(
            parser,
            (
                'meta[property="article:published_time"]',
                'meta[name="datePublished"]',
                'meta[name="publishdate"]',
            ),
            "datePublished",
        )

    def _extract_modified_date(self, parser: HTMLParser) -> str | None:
        """Extract modified/updated date from meta tags or schema.org"""
        return self._extract_meta_date(
            parser,
            (
                'meta[property="article:modified_time"]',
                'meta[name="dateModified"]',
                'meta[name="last-modified"]',
            ),
            "dateModified",
        )

    def _extract_date_from_jsonld(self, parser: HTMLParser, date_field: str) -> str | None:
        """Extract a date field from schema.org JSON-LD scripts"""
        scripts = parser.css('script[type="application/ld+json"]')
        for script in scripts:
            date_value = parse_date_from_jsonld_script(script, date_field)
            if date_value:
                return date_value
        return None

    def _extract_language_info(
        self,
        parser: HTMLParser,
        *,
        detect_language: bool,
        normalize_multilingual: bool,
    ) -> dict[str, Any]:
        """Extract language plus provenance and confidence when available."""
        if not detect_language:
            return empty_language_info()

        return (
            extract_declared_language_info(
                parser,
                normalize_multilingual=normalize_multilingual,
            )
            or detect_content_language_info(
                parser,
                normalize_multilingual=normalize_multilingual,
            )
            or empty_language_info()
        )

    def _extract_description(self, parser: HTMLParser) -> str | None:
        """Extract description from meta tags"""
        return first_meta_content(
            parser,
            (
                'meta[name="description"]',
                'meta[property="og:description"]',
                'meta[name="twitter:description"]',
            ),
        )

    def _extract_keywords(self, parser: HTMLParser) -> str | None:
        """Extract keywords from meta tags"""
        return extract_keywords(parser)

    def _extract_site_name(self, parser: HTMLParser) -> str | None:
        """Extract site name from meta tags"""
        return first_meta_content(
            parser,
            ('meta[property="og:site_name"]', 'meta[name="application-name"]'),
        )

    def _detect_content_type(self, parser: HTMLParser) -> str:
        """Detect content type based on DOM structure heuristics"""
        return detect_content_type(parser)
