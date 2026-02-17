"""
HTML extraction and cleaning module
"""

from readability import Document
from selectolax.parser import HTMLParser

from markdown_ingress.models import ExtractionResult


class Extractor:  # implements IExtractor protocol
    """Extract main content and remove unnecessary elements"""

    REMOVE_TAGS = ["script", "style", "nav", "footer", "aside", "iframe", "noscript"]

    def __init__(self, strict: bool = True):
        self.strict = strict

    def extract(self, html: str, url: str) -> ExtractionResult:
        """
        Extract main content using readability and clean DOM.
        Falls back to full-page extraction if readability returns empty content.

        Args:
            html: Raw HTML content
            url: Source URL (for readability context)

        Returns:
            ExtractionResult with cleaned HTML and metadata
        """
        # First, parse the raw HTML to remove hidden elements BEFORE readability
        tree_pre = HTMLParser(html)
        removed_hidden = self._remove_hidden_elements(tree_pre)
        pre_cleaned_html = tree_pre.html

        # Apply readability extraction on pre-cleaned HTML
        doc = Document(pre_cleaned_html)
        title = doc.title()

        # Get main content HTML
        content_html = doc.summary(html_partial=False)

        # Check if readability returned meaningful content
        # If the output is essentially empty (just <html><body></body></html>)
        # fall back to using the body of the pre-cleaned HTML
        if len(content_html.strip()) < 50 or "<body></body>" in content_html:
            # Fallback: use body from pre-cleaned HTML
            tree_fallback = HTMLParser(pre_cleaned_html)
            body = tree_fallback.css_first("body")
            if body:
                content_html = f"<html><body>{body.html}</body></html>"
            else:
                # Ultimate fallback: use entire pre-cleaned HTML
                content_html = pre_cleaned_html

        # Parse with selectolax for fast manipulation
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

        # Remove HTML comments
        self._remove_comments(tree)

        # Get cleaned HTML
        cleaned_html = tree.html

        # Extract text content
        text_content = tree.text(separator=" ", strip=True)

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
        hidden_selectors = [
            "[hidden]",
            '[aria-hidden="true"]',
            '[style*="display:none"]',
            '[style*="display: none"]',
            '[style*="visibility:hidden"]',
            '[style*="visibility: hidden"]',
        ]

        for selector in hidden_selectors:
            elements = tree.css(selector)
            count += len(elements)
            for elem in elements:
                elem.decompose()

        return count

    def _remove_comments(self, tree: HTMLParser) -> None:
        """Remove HTML comments from the tree"""
        # selectolax handles comments automatically in most cases
        # Additional comment removal can be done if needed
        pass
