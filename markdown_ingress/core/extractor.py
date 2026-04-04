"""
HTML extraction and cleaning module
"""

import logging

from readability import Document  # type: ignore[import-untyped]
from selectolax.parser import HTMLParser

from markdown_ingress.models import ExtractionResult

logger = logging.getLogger(__name__)


class Extractor:  # implements IExtractor protocol
    """Extract main content and remove unnecessary elements"""

    # Security-focused list of tags to remove completely
    # Includes XSS-dangerous elements: script, style, iframe, object, embed, applet, base
    REMOVE_TAGS = [
        "script",
        "style",
        "nav",
        "footer",
        "aside",
        "iframe",
        "noscript",
        "object",  # Security: prevents object-based XSS
        "embed",  # Security: prevents embed-based XSS
        "applet",  # Security: prevents Java applet attacks
        "base",  # Security: prevents base URL hijacking
    ]

    def __init__(self, strict: bool = True):
        self.strict = strict

    @staticmethod
    def _sanitize_html(html: str) -> str:
        """Drop control characters that break downstream HTML/XML tooling."""
        cleaned = html.replace("\x00", "")
        control_chars = {
            ord(char): None
            for char in cleaned
            if ord(char) < 32 and char not in "\t\n\r"
        }
        if control_chars:
            cleaned = cleaned.translate(control_chars)
        return cleaned

    def extract(self, html: str, url: str) -> ExtractionResult:
        """
        Extract main content using readability and clean DOM.
        Falls back to full-page extraction if readability returns empty content.

        Args:
            html: Raw HTML content
            url: Source URL (for readability context)

        Returns:
            ExtractionResult with cleaned HTML and metadata

        Raises:
            ValueError: If html is empty or contains only whitespace
        """
        # Validate input - empty HTML should be rejected early
        if not html or not html.strip():
            raise ValueError(f"Empty or whitespace-only HTML provided for URL: {url}")

        sanitized_html = self._sanitize_html(html)

        # First, parse the raw HTML to remove hidden elements BEFORE readability
        # Note: removed_hidden counts elements removed at this pre-readability stage.
        # Readability may have already excluded some hidden elements internally,
        # so this count reflects only explicit removals from the pre-parsed tree.
        tree_pre = HTMLParser(sanitized_html)
        removed_hidden = self._remove_hidden_elements(tree_pre)
        pre_cleaned_html = tree_pre.html or sanitized_html

        # Apply readability extraction on pre-cleaned HTML
        try:
            doc = Document(pre_cleaned_html)
            title = doc.title()
            content_html = doc.summary(html_partial=False)
        except Exception as e:
            # readability-lxml can raise ValueError, IndexError, AttributeError,
            # or RuntimeError on malformed HTML — fall back to raw content.
            logger.warning(
                "Readability extraction failed for URL %s, falling back to raw content: %s",
                url,
                e,
            )
            title = None
            content_html = pre_cleaned_html

        # Check if readability returned meaningful content
        # If the output is essentially empty (just <html><body></body></html>)
        # fall back to using the body of the pre-cleaned HTML
        if len(content_html.strip()) < 50 or "<body></body>" in content_html:
            # Fallback: use body from pre-cleaned HTML
            tree_fallback = HTMLParser(pre_cleaned_html)
            body = tree_fallback.css_first("body")
            if body:
                content_html = f"<html><body>{body.html or ''}</body></html>"
            else:
                # Ultimate fallback: use entire pre-cleaned HTML
                content_html = pre_cleaned_html  # pragma: no cover

        # Parse with selectolax for fast manipulation
        # Note: content_html already comes from sanitized input, so no need to re-sanitize
        tree = HTMLParser(content_html)

        # Track removed elements
        removed_tags = {}

        # Remove unwanted tags
        for tag_name in self.REMOVE_TAGS:
            elements = tree.css(tag_name)
            count = len(elements)
            if count > 0:
                removed_tags[tag_name] = count
                for elem in elements:
                    elem.decompose()

        # Security: Remove dangerous on* event handler attributes
        removed_handlers = self._remove_event_handlers(tree)
        if removed_handlers > 0:
            logger.debug(
                "Removed %d event handler attributes from %s", removed_handlers, url
            )

        # Get cleaned HTML
        cleaned_html = tree.html or ""

        # Extract text content
        text_content = tree.text(separator=" ", strip=True) or ""

        return ExtractionResult(
            html=cleaned_html,
            title=title,
            author=None,  # readability-lxml doesn't expose author in basic usage
            removed_tags=removed_tags,
            removed_hidden=removed_hidden,
            text_content=text_content,
        )

    def _remove_hidden_elements(self, tree: HTMLParser) -> int:
        """
        Remove elements that are hidden via CSS or attributes.

        Returns count of removed elements.
        """
        count = 0

        # CSS selectors for hidden elements
        # Note: Case-insensitive matching for style attributes
        hidden_selectors = [
            # Attribute-based hiding
            "[hidden]",
            '[aria-hidden="true"]',
            # Inline style display:none (case-insensitive patterns)
            '[style*="display:none"]',
            '[style*="display: none"]',
            '[style*="DISPLAY:NONE"]',
            '[style*="DISPLAY: NONE"]',
            '[style*="Display:none"]',
            '[style*="Display: None"]',
            # Inline style visibility:hidden (case-insensitive patterns)
            '[style*="visibility:hidden"]',
            '[style*="visibility: hidden"]',
            '[style*="VISIBILITY:HIDDEN"]',
            '[style*="VISIBILITY: HIDDEN"]',
            '[style*="Visibility:hidden"]',
            '[style*="Visibility: Hidden"]',
            # Visibility: collapse (table elements)
            '[style*="visibility:collapse"]',
            '[style*="visibility: collapse"]',
            '[style*="VISIBILITY:COLLAPSE"]',
            '[style*="VISIBILITY: COLLAPSE"]',
            # Opacity-based hiding
            '[style*="opacity:0"]',
            '[style*="opacity: 0"]',
            '[style*="OPACITY:0"]',
            '[style*="OPACITY: 0"]',
            '[style*="Opacity:0"]',
            '[style*="opacity:0.0"]',
            '[style*="opacity: 0.0"]',
            # Size-based hiding (height/width/font-size = 0)
            '[style*="height:0"]',
            '[style*="height: 0"]',
            '[style*="HEIGHT:0"]',
            '[style*="Height:0"]',
            '[style*="width:0"]',
            '[style*="width: 0"]',
            '[style*="WIDTH:0"]',
            '[style*="Width:0"]',
            '[style*="font-size:0"]',
            '[style*="font-size: 0"]',
            '[style*="FONT-SIZE:0"]',
            '[style*="Font-Size:0"]',
            # Position-based hiding (off-screen)
            '[style*="left:-999"]',
            '[style*="left: -999"]',
            '[style*="top:-999"]',
            '[style*="top: -999"]',
            '[style*="position:absolute"]',  # Often used with negative offsets
            '[style*="Position:Absolute"]',
            # Clip-based hiding
            '[style*="clip:rect(0"]',
            '[style*="clip: rect(0"]',
            '[style*="clip-path:inset(100%)"]',
            '[style*="clip-path: inset(100%)"]',
            '[style*="Clip-Path:inset(100%)"]',
            # Transform-based hiding
            '[style*="transform:scale(0)"]',
            '[style*="transform: scale(0)"]',
            '[style*="transform:translate(-999"]',
            '[style*="transform: translate(-999"]',
            '[style*="Transform:Scale(0)"]',
            # Content-visibility hiding
            '[style*="content-visibility:hidden"]',
            '[style*="content-visibility: hidden"]',
            '[style*="Content-Visibility:hidden"]',
            # Color-based hiding (transparent or same as background)
            '[style*="color:transparent"]',
            '[style*="color: transparent"]',
            '[style*="Color:Transparent"]',
            # Common CSS class-based hiding (selectolax supports :has, :not)
            ".hidden",
            ".invisible",
            ".hide",
            ".sr-only",  # Screen reader only - visually hidden
            ".visually-hidden",
            "[class*='hidden']",  # Any class containing 'hidden'
            "[class*='invisible']",  # Any class containing 'invisible'
            # Details element without open attribute (hidden content inside)
            "details:not([open])",
        ]

        for selector in hidden_selectors:
            try:
                elements = tree.css(selector)
                count += len(elements)
                for elem in elements:
                    elem.decompose()
            except Exception as e:
                # Some selectors might not be supported by selectolax
                logger.debug("Selector '%s' not supported or failed: %s", selector, e)
                continue

        return count

    def _remove_event_handlers(self, tree: HTMLParser) -> int:
        """
        Remove dangerous on* event handler attributes from all elements.

        Security: This prevents XSS via event handlers like onclick, onerror, onload, etc.

        Returns count of removed attributes.
        """
        count = 0
        # Iterate through all elements with attributes
        for node in tree.css("*"):
            if hasattr(node, "attributes") and node.attributes:
                # Find and remove on* event handler attributes
                attrs_to_remove = []
                for attr_name in node.attributes.keys():
                    # Match on* attributes (onclick, onerror, onload, onmouseover, etc.)
                    if attr_name.lower().startswith("on"):
                        attrs_to_remove.append(attr_name)

                for attr_name in attrs_to_remove:
                    del node.attributes[attr_name]
                    count += 1

        return count
