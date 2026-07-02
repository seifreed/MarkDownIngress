"""
Structured extraction helpers for block-level and chunk-level outputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from bs4 import BeautifulSoup, NavigableString, PageElement, Tag

from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.structured_chunks import ChunkBuilder as ChunkBuilder
from markdown_ingress.core.structured_chunks import (
    register_token_estimator_factory as register_token_estimator_factory,
)
from markdown_ingress.core.structured_metadata import (
    CODE_LANGUAGE_CLASS_PREFIXES as CODE_LANGUAGE_CLASS_PREFIXES,
)
from markdown_ingress.core.structured_metadata import build_block_metadata, detect_code_language
from markdown_ingress.core.url_safety import dangerous_url_scheme
from markdown_ingress.models import DocumentChunk, StructuredBlock

# Inline HTML tags mapped to their surrounding Markdown emphasis markers.
_INLINE_EMPHASIS = {"strong": "**", "b": "**", "em": "*", "i": "*"}

# Bound recursive blockquote nesting so pathological input cannot overflow the
# stack; deeper nesting is flattened to plain quoted text.
_MAX_BLOCKQUOTE_DEPTH = 100


def render_code_fence(code: str, language: str | None = None) -> str:
    """Render a fenced markdown code block.

    Handles code containing backticks by using more backticks in the fence
    than the maximum consecutive backticks in the content.
    """
    normalized = code.rstrip("\n")
    info = language or ""

    # Count consecutive backticks in the content to determine fence length
    max_backticks = 0
    current_count = 0
    for char in normalized:
        if char == "`":
            current_count += 1
            max_backticks = max(max_backticks, current_count)
        else:
            current_count = 0

    # Use at least 3 backticks, or more if content contains triple backticks
    fence_backticks = max(3, max_backticks + 1)

    # Add space after info string if content starts with backticks
    # to prevent the info from being interpreted as part of the code
    info_suffix = " " if normalized.startswith("`") and info else ""

    return f"{'`' * fence_backticks}{info}{info_suffix}\n{normalized}\n{'`' * fence_backticks}\n"


def render_markdown_table(rows: list[list[str]], *, has_header: bool = True) -> str:
    """Render a markdown table from normalized rows."""
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    divider = ["---"] * width
    if has_header:
        header = normalized_rows[0]
        body = normalized_rows[1:]
    else:
        header = [""] * width
        body = normalized_rows
    lines = [
        "| " + " | ".join(_escape_markdown_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join(divider) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape_markdown_table_cell(cell) for cell in row) + " |" for row in body
    )
    return "\n".join(lines) + "\n"


def _escape_markdown_table_cell(cell: str) -> str:
    """Escape table cell content so pipes and backslashes do not break columns."""
    return cell.replace("\\", "\\\\").replace("|", "\\|")


class HTMLStructureExtractor:
    """Extract structured blocks from cleaned HTML."""

    BLOCK_TAGS: Mapping[str, tuple[str, int | None]] = MappingProxyType(
        {
            "h1": ("heading", 1),
            "h2": ("heading", 2),
            "h3": ("heading", 3),
            "h4": ("heading", 4),
            "h5": ("heading", 5),
            "h6": ("heading", 6),
            "p": ("paragraph", None),
            "blockquote": ("quote", None),
            "pre": ("code", None),
            "table": ("table", None),
            "ul": ("list", None),
            "ol": ("list", None),
        }
    )
    CONTAINER_TAGS = frozenset({"pre", "table", "ul", "ol", "blockquote", "li", "td", "th"})

    def __init__(self, hasher: Hasher | None = None):
        self.hasher = hasher or Hasher()

    def extract(self, html: str) -> list[StructuredBlock]:
        """Extract logical blocks preserving headings, code and tables."""
        soup = BeautifulSoup(html, "html.parser")
        root = soup.body or soup
        blocks: list[StructuredBlock] = []
        ordinal = 0

        for element in root.descendants:
            if not isinstance(element, Tag):
                continue
            if element.name not in self.BLOCK_TAGS:
                continue
            if self._is_within_container(element):
                continue

            block_type, level = self.BLOCK_TAGS[element.name]
            markdown = self._to_markdown_block(element)
            text = self._to_text(element)
            # Keep empty table/code blocks — they're structurally significant
            if not text.strip() and block_type not in {"table", "code"}:
                continue

            block = StructuredBlock(
                block_type=block_type,
                text=text if block_type == "code" else text.strip(),
                markdown=markdown.strip() + "\n",
                ordinal=ordinal,
                level=level,
                structural_hash=self.hasher.hash_structural(markdown.strip()),
                metadata=build_block_metadata(element, block_type),
            )
            blocks.append(block)
            ordinal += 1

        return blocks

    def _to_markdown_block(self, element: Tag) -> str:
        if element.name and element.name.startswith("h"):
            level = int(element.name[1])
            return f"{'#' * level} {self._inline_markdown(element.children)}".rstrip()
        if element.name == "blockquote":
            return self._render_blockquote(element)
        if element.name == "pre":
            code = element.get_text("\n", strip=False)
            language = self._detect_code_language(element)
            return render_code_fence(code, language)
        if element.name == "table":
            rows = []
            first_row_has_th = False
            for i, tr in enumerate(element.find_all("tr")):
                cells = tr.find_all(["th", "td"])
                row = [cell.get_text(" ", strip=True) for cell in cells]
                if row:
                    if i == 0 and any(cell.name == "th" for cell in cells):
                        first_row_has_th = True
                    rows.append(row)
            return render_markdown_table(rows, has_header=first_row_has_th)
        if element.name in {"ul", "ol"}:
            return self._render_list(element)
        return self._inline_markdown(element.children)

    def _inline_markdown(
        self, nodes: Iterable[PageElement], nested_lists: list[Tag] | None = None
    ) -> str:
        """Render inline child nodes to Markdown, preserving links and emphasis.

        Keeps ``<a>`` as ``[text](href)`` (dropping dangerous-scheme hrefs like
        the main converter), ``<strong>``/``<em>`` as ``**``/``*`` and ``<code>``
        as backticks, so block/chunk markdown carries the same inline formatting
        as the primary document markdown instead of flattening to plain text.

        When ``nested_lists`` is provided, nested ``<ul>``/``<ol>`` are collected
        into it (and skipped inline) so list-item content can render them
        separately; otherwise they are ignored.
        """
        # Iterative walk (not recursion) so pathologically deep inline wrappers
        # — e.g. thousands of nested <span> — cannot blow the Python stack.
        # Closing markers are pushed onto the stack to run after a tag's children.
        out: list[str] = []
        stack: list[PageElement | str] = list(reversed(list(nodes)))
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                out.append(item)
                continue
            if isinstance(item, NavigableString):
                out.append(str(item))
                continue
            if not isinstance(item, Tag):
                continue
            self._expand_inline_tag(item, out, stack, nested_lists)
        return " ".join("".join(out).split())

    def _expand_inline_tag(
        self,
        node: Tag,
        out: list[str],
        stack: list[PageElement | str],
        nested_lists: list[Tag] | None,
    ) -> None:
        if node.name in {"ul", "ol"}:
            if nested_lists is not None:
                nested_lists.append(node)
            return
        if node.name == "code":
            out.append(f"`{node.get_text()}`")
            return
        if node.name == "br":
            out.append(" ")
            return
        if node.name == "a":
            href = node.get("href")
            if isinstance(href, str) and href.strip() and dangerous_url_scheme(href) is None:
                out.append("[")
                stack.append(f"]({href.strip()})")
            stack.extend(reversed(list(node.children)))
            return
        if node.name in _INLINE_EMPHASIS and node.get_text(strip=True):
            marker = _INLINE_EMPHASIS[node.name]
            out.append(marker)
            stack.append(marker)
            stack.extend(reversed(list(node.children)))
            return
        if node.name in _INLINE_EMPHASIS:
            return  # empty emphasis renders nothing
        stack.extend(reversed(list(node.children)))

    def _to_text(self, element: Tag) -> str:
        if element.name == "pre":
            return element.get_text("\n", strip=False).rstrip("\n")
        if element.name in {"ul", "ol"}:
            return self._render_list(element)
        return element.get_text(" ", strip=True)

    def _render_blockquote(self, element: Tag, depth: int = 0) -> str:
        """Render a blockquote, adding a '> ' level per nesting depth.

        Mirrors the main converter ('> > deep' for nested quotes, blank '>'
        between paragraphs). A depth guard flattens pathologically deep nesting
        to plain quoted text so malicious input cannot overflow the stack.
        """
        if depth >= _MAX_BLOCKQUOTE_DEPTH:
            return f"> {' '.join(element.get_text(' ').split())}".rstrip()
        groups: list[list[str]] = []
        for child in element.children:
            if isinstance(child, Tag) and child.name == "blockquote":
                groups.append(self._render_blockquote(child, depth + 1).splitlines())
            elif isinstance(child, Tag) and child.name in {"ul", "ol"}:
                groups.append(self._render_list(child).splitlines())
            else:
                inline = self._inline_markdown([child])
                if inline.strip():
                    groups.append(inline.strip().splitlines())
        lines: list[str] = []
        for index, group in enumerate(groups):
            if index > 0:
                lines.append("")
            lines.extend(group)
        return "\n".join(f"> {line}".rstrip() for line in lines)

    def _render_list(self, element: Tag) -> str:
        lines: list[str] = []
        self._append_list_lines(element, lines, depth=0)
        return "\n".join(lines)

    def _append_list_lines(self, element: Tag, lines: list[str], *, depth: int) -> None:
        stack: list[tuple[Tag, str, list[Tag], int]] = [
            (element, "  " * depth, list(element.find_all("li", recursive=False)), 0)
        ]
        while stack:
            list_element, indent, items, item_index = stack.pop()
            if item_index >= len(items):
                continue

            item = items[item_index]
            stack.append((list_element, indent, items, item_index + 1))

            ordered = list_element.name == "ol"
            index = item_index + 1
            prefix = f"{index}." if ordered else "-"
            direct_text, nested_lists = self._list_item_content(item)
            line = f"{indent}{prefix}"
            if direct_text:
                line = f"{line} {direct_text}"
            lines.append(line)
            # Indent nested items to align under the parent marker's content
            # column ("- " -> 2, "1. " -> 3, "10. " -> 4) so nested ordered lists
            # stay valid CommonMark children, matching the main converter.
            child_indent = indent + " " * (len(prefix) + 1)
            for nested_list in reversed(nested_lists):
                stack.append(
                    (
                        nested_list,
                        child_indent,
                        list(nested_list.find_all("li", recursive=False)),
                        0,
                    )
                )

    def _list_item_content(self, item: Tag) -> tuple[str, list[Tag]]:
        nested_lists: list[Tag] = []
        direct = self._inline_markdown(item.children, nested_lists)
        return direct, nested_lists

    def _detect_code_language(self, element: Tag) -> str | None:
        return detect_code_language(element)

    def _is_within_container(self, element: Tag) -> bool:
        """Return whether a block tag is nested inside a container we already serialize.

        We emit aggregate blocks for containers like lists, tables, quotes, and code
        blocks, so nested paragraphs/headings inside them would otherwise be duplicated.
        """
        parent = element.parent
        while isinstance(parent, Tag):
            if parent.name in self.CONTAINER_TAGS:
                return True
            parent = parent.parent
        return False


def blocks_to_dicts(blocks: list[StructuredBlock]) -> list[dict]:
    """Serialize blocks for metadata and API responses."""
    return [block.to_dict() for block in blocks]


def chunks_to_dicts(chunks: list[DocumentChunk]) -> list[dict]:
    """Serialize chunks for metadata and API responses."""
    return [chunk.to_dict() for chunk in chunks]
