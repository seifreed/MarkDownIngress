"""
Metadata extraction from HTML documents
"""

import logging
from typing import Any, cast

from selectolax.parser import HTMLParser

from markdown_ingress.core.metadata_jsonld import (
    parse_author_from_jsonld_script,
    parse_date_from_jsonld_script,
)
from markdown_ingress.core.metadata_urls import extract_canonical_url

logger = logging.getLogger(__name__)


def _empty_language_info() -> dict[str, Any]:
    return {"language": None, "source": None, "confidence": None}


def _language_info(language: str, source: str, confidence: float) -> dict[str, Any]:
    return {"language": language, "source": source, "confidence": confidence}


def _normalize_language_code(value: str, *, normalize_multilingual: bool) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    # Content-language and lang attributes can contain comma-separated
    # language lists. Use the primary declared language instead of returning
    # the whole list as if it were a single code.
    raw = raw.split(",", 1)[0].strip()
    if not raw:
        return ""
    return raw.split("-")[0] if normalize_multilingual else raw


def _language_info_from_value(
    value: str,
    *,
    source: str,
    confidence: float,
    normalize_multilingual: bool,
) -> dict[str, Any] | None:
    lang_code = _normalize_language_code(value, normalize_multilingual=normalize_multilingual)
    if lang_code:
        return _language_info(lang_code, source, confidence)
    return None


def _extract_declared_language_info(
    parser: HTMLParser,
    *,
    normalize_multilingual: bool,
) -> dict[str, Any] | None:
    html_tag = parser.css_first("html")
    if html_tag:
        lang = (html_tag.attributes.get("lang") or "").strip()
        if lang:
            return _language_info_from_value(
                lang,
                source="html_lang",
                confidence=1.0,
                normalize_multilingual=normalize_multilingual,
            )

    meta_lang = parser.css_first('meta[http-equiv="content-language"]')
    if meta_lang:
        content = (meta_lang.attributes.get("content") or "").strip()
        if content:
            return _language_info_from_value(
                content,
                source="meta_content_language",
                confidence=0.95,
                normalize_multilingual=normalize_multilingual,
            )
    return None


def _detect_content_language_info(
    parser: HTMLParser,
    *,
    normalize_multilingual: bool,
) -> dict[str, Any] | None:
    try:
        from langdetect import detect  # type: ignore[import-untyped]
        from langdetect.lang_detect_exception import (  # type: ignore[import-untyped]
            LangDetectException,
        )

        body = parser.css_first("body")
        if body:
            text = body.text(strip=True)
            if text and len(text) > 50:
                detected_lang = _normalize_language_code(
                    cast(str, detect(text)),
                    normalize_multilingual=normalize_multilingual,
                )
                if detected_lang:
                    return _language_info(detected_lang, "langdetect", 0.6)
    except ImportError:
        pass  # langdetect not installed — graceful degradation
    except (AttributeError, LangDetectException, TypeError, ValueError) as exc:
        logger.debug("langdetect failed: %s", exc)
    return None


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
        # Try meta author tag
        meta_author = parser.css_first('meta[name="author"]')
        if meta_author:
            content = (meta_author.attributes.get("content") or "").strip()
            if content:
                return content

        # Try article:author (Open Graph)
        og_author = parser.css_first('meta[property="article:author"]')
        if og_author:
            content = (og_author.attributes.get("content") or "").strip()
            if content:
                return content

        # Try schema.org JSON-LD
        return self._extract_author_from_jsonld(parser)

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
        for selector in selectors:
            node = parser.css_first(selector)
            if node:
                content = (node.attributes.get("content") or "").strip()
                if content:
                    return content
        return self._extract_date_from_jsonld(parser, jsonld_field)

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
            return _empty_language_info()

        return (
            _extract_declared_language_info(
                parser,
                normalize_multilingual=normalize_multilingual,
            )
            or _detect_content_language_info(
                parser,
                normalize_multilingual=normalize_multilingual,
            )
            or _empty_language_info()
        )

    def _extract_description(self, parser: HTMLParser) -> str | None:
        """Extract description from meta tags"""
        # Try meta description
        meta_desc = parser.css_first('meta[name="description"]')
        if meta_desc:
            content = (meta_desc.attributes.get("content") or "").strip()
            if content:
                return content

        # Try og:description
        og_desc = parser.css_first('meta[property="og:description"]')
        if og_desc:
            content = (og_desc.attributes.get("content") or "").strip()
            if content:
                return content

        # Try twitter:description
        twitter_desc = parser.css_first('meta[name="twitter:description"]')
        if twitter_desc:
            content = (twitter_desc.attributes.get("content") or "").strip()
            if content:
                return content

        return None

    def _extract_keywords(self, parser: HTMLParser) -> str | None:
        """Extract keywords from meta tags"""
        # Try meta keywords
        meta_keywords = parser.css_first('meta[name="keywords"]')
        if meta_keywords:
            content = (meta_keywords.attributes.get("content") or "").strip()
            if content:
                return content

        # Try article:tag (Open Graph)
        og_tags = parser.css('meta[property="article:tag"]')
        if og_tags:
            tags = [(tag.attributes.get("content") or "").strip() for tag in og_tags]
            tags = [t for t in tags if t]
            if tags:
                return ", ".join(tags)

        return None

    def _extract_site_name(self, parser: HTMLParser) -> str | None:
        """Extract site name from meta tags"""
        # Try og:site_name
        og_site = parser.css_first('meta[property="og:site_name"]')
        if og_site:
            content = (og_site.attributes.get("content") or "").strip()
            if content:
                return content

        # Try application-name
        app_name = parser.css_first('meta[name="application-name"]')
        if app_name:
            content = (app_name.attributes.get("content") or "").strip()
            if content:
                return content

        return None

    def _detect_content_type(self, parser: HTMLParser) -> str:
        """Detect content type based on DOM structure heuristics"""
        # Check for article indicators
        if (
            parser.css_first("article")
            or parser.css_first('meta[property="og:type"][content="article"]')
            or parser.css_first(".post")
            or parser.css_first(".article")
        ):
            return "article"

        # Check for documentation indicators
        if (
            parser.css_first(".documentation")
            or parser.css_first("#docs")
            or parser.css_first(".docs")
            or parser.css_first(".api-doc")
            or parser.css_first(".api-docs")
            or parser.css_first(".api-documentation")
        ):
            return "documentation"

        # Check for forum/discussion indicators
        if (
            parser.css_first(".forum")
            or parser.css_first(".discussion")
            or parser.css_first(".thread")
            or parser.css_first(".comment")
            or parser.css_first(".comments")
            or parser.css_first(".comment-list")
            or parser.css_first(".comment-thread")
        ):
            return "forum"

        # Check for e-commerce indicators
        if (
            parser.css_first(".product")
            or parser.css_first('[itemtype*="Product"]')
            or parser.css_first(".price")
            or parser.css_first(".add-to-cart")
        ):
            return "ecommerce"

        # Default
        return "webpage"
