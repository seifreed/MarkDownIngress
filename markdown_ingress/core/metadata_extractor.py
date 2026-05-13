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

logger = logging.getLogger(__name__)


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
            html,
            detect_language=detect_language,
            normalize_multilingual=normalize_multilingual,
        )
        metadata = {
            "author": self._extract_author(parser),
            "published_date": self._extract_published_date(parser),
            "modified_date": self._extract_modified_date(parser),
            "language": language_info["language"],
            "language_source": language_info["source"],
            "language_confidence": language_info["confidence"],
            "description": self._extract_description(parser),
            "keywords": self._extract_keywords(parser),
            "canonical_url": self._extract_canonical_url(parser, url),
            "site_name": self._extract_site_name(parser),
            "content_type": self._detect_content_type(parser),
        }

        return metadata

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

    def _extract_published_date(self, parser: HTMLParser) -> str | None:
        """Extract published date from meta tags or schema.org"""
        # Try article:published_time (Open Graph)
        og_published = parser.css_first('meta[property="article:published_time"]')
        if og_published:
            content = (og_published.attributes.get("content") or "").strip()
            if content:
                return content

        # Try meta datePublished
        meta_published = parser.css_first('meta[name="datePublished"]')
        if meta_published:
            content = (meta_published.attributes.get("content") or "").strip()
            if content:
                return content

        # Try meta publishdate
        meta_publishdate = parser.css_first('meta[name="publishdate"]')
        if meta_publishdate:
            content = (meta_publishdate.attributes.get("content") or "").strip()
            if content:
                return content

        # Try schema.org JSON-LD
        return self._extract_date_from_jsonld(parser, "datePublished")

    def _extract_modified_date(self, parser: HTMLParser) -> str | None:
        """Extract modified/updated date from meta tags or schema.org"""
        # Try article:modified_time (Open Graph)
        og_modified = parser.css_first('meta[property="article:modified_time"]')
        if og_modified:
            content = (og_modified.attributes.get("content") or "").strip()
            if content:
                return content

        # Try meta dateModified
        meta_modified = parser.css_first('meta[name="dateModified"]')
        if meta_modified:
            content = (meta_modified.attributes.get("content") or "").strip()
            if content:
                return content

        # Try meta last-modified
        meta_lastmod = parser.css_first('meta[name="last-modified"]')
        if meta_lastmod:
            content = (meta_lastmod.attributes.get("content") or "").strip()
            if content:
                return content

        # Try schema.org JSON-LD
        return self._extract_date_from_jsonld(parser, "dateModified")

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
        html: str,
        *,
        detect_language: bool,
        normalize_multilingual: bool,
    ) -> dict[str, Any]:
        """Extract language plus provenance and confidence when available."""
        if not detect_language:
            return {"language": None, "source": None, "confidence": None}

        def normalize_lang(value: str) -> str:
            raw = value.strip().lower()
            if not raw:
                return ""
            # Content-language and lang attributes can contain comma-separated
            # language lists. Use the primary declared language instead of
            # returning the whole list as if it were a single code.
            raw = raw.split(",", 1)[0].strip()
            if not raw:
                return ""
            return raw.split("-")[0] if normalize_multilingual else raw

        # Try html lang attribute
        html_tag = parser.css_first("html")
        if html_tag:
            lang = (html_tag.attributes.get("lang") or "").strip()
            if lang:
                lang_code = normalize_lang(lang)
                if lang_code:
                    return {"language": lang_code, "source": "html_lang", "confidence": 1.0}

        # Try meta content-language
        meta_lang = parser.css_first('meta[http-equiv="content-language"]')
        if meta_lang:
            content = (meta_lang.attributes.get("content") or "").strip()
            if content:
                lang_code = normalize_lang(content)
                if lang_code:
                    return {
                        "language": lang_code,
                        "source": "meta_content_language",
                        "confidence": 0.95,
                    }

        # Fallback: detect from content using langdetect
        try:
            from langdetect import detect  # type: ignore[import-untyped]

            # Get text content for detection
            body = parser.css_first("body")
            if body:
                text = body.text(strip=True)
                # Only detect if we have meaningful text
                if text and len(text) > 50:
                    detected_lang = normalize_lang(cast(str, detect(text)))
                    if detected_lang:
                        return {
                            "language": detected_lang,
                            "source": "langdetect",
                            "confidence": 0.6,
                        }
        except ImportError:
            pass  # langdetect not installed — graceful degradation
        except Exception as exc:
            logger.debug("langdetect failed: %s", exc)

        return {"language": None, "source": None, "confidence": None}

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

    def _extract_canonical_url(self, parser: HTMLParser, url: str) -> str | None:
        """Extract canonical URL from link tag."""
        from urllib.parse import urljoin, urlsplit

        def _validate_http_url(candidate: str) -> str | None:
            parsed = urlsplit(candidate)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return None
            return candidate

        def _resolve_base_url() -> str:
            base_tag = parser.css_first("base[href]")
            if not base_tag:
                return url
            href = (base_tag.attributes.get("href") or "").strip()
            if not href:
                return url
            return _validate_http_url(urljoin(url, href)) or url

        def _resolve_candidate_url(candidate: str, base_url: str) -> str | None:
            return _validate_http_url(urljoin(base_url, candidate))

        def _iter_links_with_rel_token(token: str):
            normalized_token = token.lower()
            for link in parser.css("link[rel]"):
                rel = link.attributes.get("rel") or ""
                rel_tokens = {part.lower() for part in rel.split() if part}
                if normalized_token in rel_tokens:
                    yield link

        base_url = _resolve_base_url()

        # Try link rel=canonical
        for canonical_link in _iter_links_with_rel_token("canonical"):
            href = (canonical_link.attributes.get("href") or "").strip()
            if href:
                resolved = _resolve_candidate_url(href, base_url)
                if resolved is not None:
                    return resolved

        # Try og:url
        og_url = parser.css_first('meta[property="og:url"]')
        if og_url:
            content = (og_url.attributes.get("content") or "").strip()
            if content:
                resolved = _resolve_candidate_url(content, base_url)
                if resolved is not None:
                    return resolved

        # Fallback to original URL
        return url

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
