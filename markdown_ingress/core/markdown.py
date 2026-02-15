"""
Markdown conversion module
"""

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from markdown_ingress.core.normalizer import Normalizer


class MarkdownConverter:
    """Convert cleaned HTML to sanitized Markdown"""

    def __init__(self):
        self.normalizer = Normalizer()

    def convert(self, html: str) -> str:
        """
        Convert HTML to clean Markdown.

        Args:
            html: Cleaned HTML content

        Returns:
            Markdown string without inline HTML
        """
        # First normalize URLs in links
        html = self._normalize_links(html)

        # Convert to markdown with strict settings
        markdown = md(
            html,
            heading_style="ATX",  # Use # style headings
            bullets="-",  # Use - for unordered lists
            strip=["script", "style"],  # Extra safety
            escape_asterisks=False,  # Don't escape markdown
            escape_underscores=False,
        )

        # Normalize the markdown content
        markdown = self.normalizer.normalize(markdown)

        # Clean up markdown artifacts
        markdown = self._clean_markdown(markdown)

        return markdown

    def _normalize_links(self, html: str) -> str:
        """Normalize URLs in anchor tags"""
        soup = BeautifulSoup(html, "html.parser")

        for link in soup.find_all("a", href=True):
            original_href = link["href"]
            normalized_href = self.normalizer.normalize_url(original_href)
            link["href"] = normalized_href

        return str(soup)

    def _clean_markdown(self, markdown: str) -> str:
        """
        Clean up markdown output artifacts.

        Args:
            markdown: Raw markdown output

        Returns:
            Cleaned markdown
        """
        import re

        # Remove excessive blank lines (more than 2)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)

        # Ensure headings have space after #
        markdown = re.sub(r"^(#{1,6})(\S)", r"\1 \2", markdown, flags=re.MULTILINE)

        # Remove trailing spaces on lines
        markdown = re.sub(r"[ \t]+$", "", markdown, flags=re.MULTILINE)

        # Ensure consistent list spacing
        # No blank lines within list items
        markdown = re.sub(r"(\n[-*+]\s.+)\n\n(\s*[-*+]\s)", r"\1\n\2", markdown)

        return markdown.strip() + "\n"
