"""Markdown conversion adapter using markdownify and BeautifulSoup."""

from __future__ import annotations

import uuid
from typing import Any

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from markdown_ingress.adapters.normalizing.normalizer import Normalizer
from markdown_ingress.core.interfaces import IMarkdownConverter
from markdown_ingress.core.structured import (
    CODE_LANGUAGE_CLASS_PREFIXES,
    render_code_fence,
    render_markdown_table,
)
from markdown_ingress.core.url_safety import dangerous_url_scheme


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
        self._normalize_links(soup)
        self._protect_code_blocks(soup, placeholders)
        self._protect_tables(soup, placeholders)
        return str(soup), placeholders

    def _normalize_links(self, soup: Any) -> None:
        for link in soup.find_all("a", href=True):
            original_href = link.get("href")
            if not isinstance(original_href, str):
                continue
            normalized_href = self.normalizer.normalize_url(original_href)
            if dangerous_url_scheme(normalized_href) is not None:
                del link["href"]
                continue
            link["href"] = normalized_href

    def _protect_code_blocks(self, soup: Any, placeholders: dict[str, str]) -> None:
        for pre in soup.find_all("pre"):
            code = pre.get_text(strip=False)
            language = self._detect_code_language(pre)
            token = f"\x00MDI_CODE_{uuid.uuid4().hex}\x00"
            rendered = render_code_fence(code, language)
            if not rendered.endswith("\n"):
                rendered += "\n"
            placeholders[token] = rendered
            pre.replace_with(token)

    def _detect_code_language(self, pre: Any) -> str | None:
        raw_classes = self._collect_code_classes(pre)
        for prefix in CODE_LANGUAGE_CLASS_PREFIXES:
            for class_name in raw_classes:
                if class_name.startswith(prefix):
                    return class_name[len(prefix) :]
        return None

    def _collect_code_classes(self, pre: Any) -> list[str]:
        code_tag = pre.find("code")
        raw_classes: list[str] = []
        for element in (pre, code_tag):
            if element is None:
                continue
            candidates = element.get("class")
            if isinstance(candidates, str):
                raw_classes.append(candidates)
            else:
                raw_classes.extend(
                    class_name
                    for class_name in list(candidates or [])
                    if isinstance(class_name, str)
                )
        return raw_classes

    def _protect_tables(self, soup: Any, placeholders: dict[str, str]) -> None:
        for table in soup.find_all("table"):
            rows, first_row_has_th = self._extract_table_rows(table)
            token = f"\x00MDI_TABLE_{uuid.uuid4().hex}\x00"
            placeholders[token] = render_markdown_table(rows, has_header=first_row_has_th).strip()
            table.replace_with(token)

    def _extract_table_rows(self, table: Any) -> tuple[list[list[str]], bool]:
        rows: list[list[str]] = []
        first_row_has_th = False
        for index, table_row in enumerate(table.find_all("tr")):
            cells = table_row.find_all(["th", "td"])
            row = [cell.get_text(" ", strip=True) for cell in cells]
            if not row:
                continue
            if index == 0 and any(cell.name == "th" for cell in cells):
                first_row_has_th = True
            rows.append(row)
        return rows, first_row_has_th

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
            markdown = re.sub(r"(\n[-*+]\s.+)\n\n(\s*[-*+]\s.+)", r"\1\n\2", markdown)

        return markdown.strip() + "\n"
