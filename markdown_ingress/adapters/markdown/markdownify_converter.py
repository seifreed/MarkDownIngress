"""Markdown conversion adapter using markdownify and BeautifulSoup."""

from __future__ import annotations

import uuid

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from markdown_ingress.core.interfaces import IMarkdownConverter
from markdown_ingress.core.normalizer import Normalizer
from markdown_ingress.core.structured import render_code_fence, render_markdown_table


class MarkdownConverter(IMarkdownConverter):
    """Convert cleaned HTML to sanitized Markdown."""

    def __init__(self):
        self.normalizer = Normalizer()

    def convert(self, html: str) -> str:
        html, placeholders = self._prepare_html(html)

        markdown = md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"],
            escape_asterisks=False,
            escape_underscores=False,
        )

        markdown = self.normalizer.normalize(markdown)
        markdown = self._clean_markdown(markdown)
        markdown = self._restore_placeholders(markdown, placeholders)

        if not markdown.endswith("\n"):
            markdown += "\n"

        return markdown

    def _prepare_html(self, html: str) -> tuple[str, dict[str, str]]:
        """Normalize links and protect complex structures before markdownify."""
        soup = BeautifulSoup(html, "html.parser")
        placeholders: dict[str, str] = {}

        for link in soup.find_all("a", href=True):
            original_href = link["href"]
            normalized_href = self.normalizer.normalize_url(original_href)
            link["href"] = normalized_href

        for index, pre in enumerate(soup.find_all("pre"), start=1):
            code = pre.get_text(strip=False)
            language = None
            code_tag = pre.find("code")
            if code_tag:
                raw_classes = code_tag.get("class")
                if isinstance(raw_classes, str):
                    classes: list[str] = [raw_classes]
                else:
                    classes = list(raw_classes or [])
                for _pfx in ("language-", "lang-", "highlight-"):
                    for class_name in classes:
                        if class_name.startswith(_pfx):
                            language = class_name[len(_pfx):]
                            break
                    if language:
                        break
            token = f"\x00MDI_CODE_{uuid.uuid4().hex}\x00"
            rendered = render_code_fence(code, language)
            if not rendered.endswith("\n"):
                rendered += "\n"
            placeholders[token] = rendered
            pre.replace_with(token)

        for index, table in enumerate(soup.find_all("table"), start=1):
            rows = []
            first_row_has_th = False
            for i, tr in enumerate(table.find_all("tr")):
                cells = tr.find_all(["th", "td"])
                row = [cell.get_text(" ", strip=True) for cell in cells]
                if row:
                    if i == 0 and any(cell.name == "th" for cell in cells):
                        first_row_has_th = True
                    rows.append(row)
            token = f"\x00MDI_TABLE_{uuid.uuid4().hex}\x00"
            placeholders[token] = render_markdown_table(rows, has_header=first_row_has_th).strip()
            table.replace_with(token)

        return str(soup), placeholders

    def _restore_placeholders(self, markdown: str, placeholders: dict[str, str]) -> str:
        """Restore protected technical blocks into markdown output."""
        for token, replacement in placeholders.items():
            markdown = markdown.replace(token, replacement)
        return markdown

    def _clean_markdown(self, markdown: str) -> str:
        import re

        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        markdown = re.sub(r"^(#{1,6})(\S)", r"\1 \2", markdown, flags=re.MULTILINE)
        markdown = re.sub(r"[ \t]+$", "", markdown, flags=re.MULTILINE)

        prev = None
        for _ in range(50):
            if prev == markdown:
                break
            prev = markdown
            markdown = re.sub(r"(\n[-*+]\s.+)\n\n(\s*[-*+]\s)", r"\1\n\2", markdown)

        return markdown.strip() + "\n"
