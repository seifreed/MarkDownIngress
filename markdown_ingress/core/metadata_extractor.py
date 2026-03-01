"""
Metadata extraction from HTML documents
"""

import json
from typing import Any

from selectolax.parser import HTMLParser


class MetadataExtractor:
    """Extract rich metadata from HTML documents"""

    def __init__(self):
        """Initialize metadata extractor"""
        pass

    def extract(self, html: str, url: str) -> dict[str, Any]:
        """
        Extract comprehensive metadata from HTML.

        Args:
            html: HTML content
            url: URL of the document

        Returns:
            Dictionary with extracted metadata fields
        """
        parser = HTMLParser(html)

        metadata = {
            "author": self._extract_author(parser),
            "published_date": self._extract_published_date(parser),
            "modified_date": self._extract_modified_date(parser),
            "language": self._extract_language(parser, html),
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
            author = self._parse_author_from_script(script)
            if author:
                return author
        return None

    def _parse_author_from_script(self, script) -> str | None:
        """Parse author from a single JSON-LD script tag"""
        try:
            data = json.loads(script.text())
        except (json.JSONDecodeError, AttributeError):
            return None

        if not isinstance(data, dict):
            return None

        author = data.get("author")
        if not author:
            return None

        if isinstance(author, dict):
            return author.get("name", "")
        if isinstance(author, str):
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
            date_value = self._parse_date_from_script(script, date_field)
            if date_value:
                return date_value
        return None

    def _parse_date_from_script(self, script, date_field: str) -> str | None:
        """Parse a date field from a single JSON-LD script tag"""
        try:
            data = json.loads(script.text())
        except (json.JSONDecodeError, AttributeError):
            return None

        if not isinstance(data, dict):
            return None

        date_value = data.get(date_field)
        return date_value if date_value else None

    def _extract_language(self, parser: HTMLParser, html: str) -> str | None:
        """Extract language from html lang attribute, then detect from content"""
        # Try html lang attribute
        html_tag = parser.css_first("html")
        if html_tag:
            lang = (html_tag.attributes.get("lang") or "").strip()
            if lang:
                # Normalize language code (e.g., en-US -> en)
                lang_code = lang.split("-")[0].lower()
                if lang_code:
                    return lang_code

        # Try meta content-language
        meta_lang = parser.css_first('meta[http-equiv="content-language"]')
        if meta_lang:
            content = (meta_lang.attributes.get("content") or "").strip()
            if content:
                lang_code = content.split("-")[0].lower()
                if lang_code:
                    return lang_code

        # Fallback: detect from content using langdetect
        try:
            from langdetect import detect

            # Get text content for detection
            body = parser.css_first("body")
            if body:
                text = body.text(strip=True)
                # Only detect if we have meaningful text
                if text and len(text) > 50:
                    detected_lang = detect(text)
                    return detected_lang
        except (ImportError, Exception):
            pass

        return None

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
        """Extract canonical URL from link tag"""
        # Try link rel=canonical
        canonical_link = parser.css_first('link[rel="canonical"]')
        if canonical_link:
            href = (canonical_link.attributes.get("href") or "").strip()
            if href:
                return href

        # Try og:url
        og_url = parser.css_first('meta[property="og:url"]')
        if og_url:
            content = (og_url.attributes.get("content") or "").strip()
            if content:
                return content

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
            or parser.css_first('[class*="api-doc"]')
        ):
            return "documentation"

        # Check for forum/discussion indicators
        if (
            parser.css_first(".forum")
            or parser.css_first(".discussion")
            or parser.css_first(".thread")
            or parser.css_first('[class*="comment"]')
        ):
            return "forum"

        # Check for e-commerce indicators
        if (
            parser.css_first(".product")
            or parser.css_first('[itemtype*="Product"]')
            or parser.css_first(".price")
            or parser.css_first('[class*="add-to-cart"]')
        ):
            return "ecommerce"

        # Default
        return "webpage"
