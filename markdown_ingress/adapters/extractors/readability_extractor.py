"""HTML extraction adapter using readability-lxml and selectolax."""

import logging
import re

from readability import Document  # type: ignore[import-untyped]
from selectolax.parser import HTMLParser

from markdown_ingress.adapters.extractors.readability_sanitization import (
    remove_hidden_elements,
    sanitize_dangerous_content,
)
from markdown_ingress.core.interfaces import IExtractor
from markdown_ingress.models import ExtractionResult

logger = logging.getLogger(__name__)


def _is_empty_body(html: str) -> bool:
    """Detect an empty/whitespace-only body element (case-insensitive)."""
    return bool(re.search(r"<\s*body\s*>\s*<\s*/\s*body\s*>", html, re.IGNORECASE))


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")


class Extractor(IExtractor):
    """Extract main content and remove unnecessary elements."""

    REMOVE_TAGS = (
        "script",
        "style",
        "nav",
        "footer",
        "aside",
        "iframe",
        "noscript",
        "object",
        "embed",
        "applet",
        "base",
    )

    def __init__(self, strict: bool = True):
        self.strict = strict

    @staticmethod
    def _sanitize_html(html: str) -> str:
        """Drop control characters that break downstream HTML/XML tooling."""
        return _CONTROL_CHARS_RE.sub("", html)

    def extract(self, html: str, url: str) -> ExtractionResult:
        if not html or not html.strip():
            raise ValueError(f"Empty or whitespace-only HTML provided for URL: {url}")

        sanitized_html = self._sanitize_html(html)

        tree_pre = HTMLParser(sanitized_html)
        removed_hidden = self._remove_hidden_elements(tree_pre)
        pre_cleaned_html = tree_pre.html or sanitized_html

        try:
            doc = Document(pre_cleaned_html)
            title = doc.title()
            content_html = doc.summary(html_partial=False)
        except Exception as e:  # noqa: BLE001 - readability failures fall back to raw HTML
            logger.warning(
                "Readability extraction failed for URL %s, falling back to raw content: %s",
                url,
                e,
            )
            title = None
            content_html = pre_cleaned_html

        if len(content_html.strip()) < 50 or _is_empty_body(content_html):
            tree_fallback = HTMLParser(pre_cleaned_html)
            body = tree_fallback.css_first("body")
            if body:
                content_html = f"<html><body>{body.html or ''}</body></html>"
            else:
                content_html = pre_cleaned_html  # pragma: no cover

        tree = HTMLParser(content_html)
        removed_tags = {}

        for tag_name in self.REMOVE_TAGS:
            elements = tree.css(tag_name)
            count = len(elements)
            if count > 0:
                removed_tags[tag_name] = count
                for elem in elements:
                    elem.decompose()

        sanitize_stats = self._sanitize_dangerous_content(tree)
        total_removed = sum(sanitize_stats.values())
        if total_removed > 0:
            logger.debug(
                "Sanitized %d dangerous items from %s: %d event handlers, %d javascript URLs, "
                "%d style attributes, %d data URLs, %d vbscript URLs",
                total_removed,
                url,
                sanitize_stats["event_handlers"],
                sanitize_stats["javascript_urls"],
                sanitize_stats["style_attributes"],
                sanitize_stats["data_urls"],
                sanitize_stats["vbscript_urls"],
            )

        cleaned_html = tree.html or ""
        text_content = tree.text(separator=" ", strip=True) or ""

        return ExtractionResult(
            html=cleaned_html,
            title=title,
            author=None,
            removed_tags=removed_tags,
            removed_hidden=removed_hidden,
            text_content=text_content,
        )

    def _remove_hidden_elements(self, tree: HTMLParser) -> int:
        return remove_hidden_elements(tree)

    def _sanitize_dangerous_content(self, tree: HTMLParser) -> dict[str, int]:
        return sanitize_dangerous_content(tree)
