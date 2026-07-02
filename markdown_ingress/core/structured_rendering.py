"""Markdown rendering helpers for structured HTML blocks."""

from __future__ import annotations

from collections.abc import Iterable

from bs4 import NavigableString, PageElement, Tag

from markdown_ingress.core.structured_metadata import detect_code_language
from markdown_ingress.core.url_safety import dangerous_url_scheme

_INLINE_EMPHASIS = {"strong": "**", "b": "**", "em": "*", "i": "*"}
_MAX_BLOCKQUOTE_DEPTH = 100


def render_code_fence(code: str, language: str | None = None) -> str:
    """Render a fenced markdown code block."""
    normalized = code.rstrip("\n")
    info = language or ""
    max_backticks = 0
    current_count = 0
    for char in normalized:
        if char == "`":
            current_count += 1
            max_backticks = max(max_backticks, current_count)
        else:
            current_count = 0
    fence_backticks = max(3, max_backticks + 1)
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


def render_markdown_block(element: Tag) -> str:
    if element.name and element.name.startswith("h"):
        level = int(element.name[1])
        return f"{'#' * level} {render_inline_markdown(element.children)}".rstrip()
    if element.name == "blockquote":
        return _render_blockquote(element)
    if element.name == "pre":
        code = element.get_text("\n", strip=False)
        return render_code_fence(code, detect_code_language(element))
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
        return _render_list(element)
    return render_inline_markdown(element.children)


def render_block_text(element: Tag) -> str:
    if element.name == "pre":
        return element.get_text("\n", strip=False).rstrip("\n")
    if element.name in {"ul", "ol"}:
        return _render_list(element)
    return element.get_text(" ", strip=True)


def render_inline_markdown(
    nodes: Iterable[PageElement], nested_lists: list[Tag] | None = None
) -> str:
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
        _expand_inline_tag(item, out, stack, nested_lists)
    return " ".join("".join(out).split())


def _escape_markdown_table_cell(cell: str) -> str:
    return cell.replace("\\", "\\\\").replace("|", "\\|")


def _expand_inline_tag(
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
        return
    stack.extend(reversed(list(node.children)))


def _render_blockquote(element: Tag, depth: int = 0) -> str:
    if depth >= _MAX_BLOCKQUOTE_DEPTH:
        return f"> {' '.join(element.get_text(' ').split())}".rstrip()
    groups: list[list[str]] = []
    for child in element.children:
        if isinstance(child, Tag) and child.name == "blockquote":
            groups.append(_render_blockquote(child, depth + 1).splitlines())
        elif isinstance(child, Tag) and child.name in {"ul", "ol"}:
            groups.append(_render_list(child).splitlines())
        else:
            inline = render_inline_markdown([child])
            if inline.strip():
                groups.append(inline.strip().splitlines())
    lines: list[str] = []
    for index, group in enumerate(groups):
        if index > 0:
            lines.append("")
        lines.extend(group)
    return "\n".join(f"> {line}".rstrip() for line in lines)


def _render_list(element: Tag) -> str:
    lines: list[str] = []
    stack: list[tuple[Tag, str, list[Tag], int]] = [
        (element, "", list(element.find_all("li", recursive=False)), 0)
    ]
    while stack:
        list_element, indent, items, item_index = stack.pop()
        if item_index >= len(items):
            continue
        item = items[item_index]
        stack.append((list_element, indent, items, item_index + 1))

        prefix = f"{item_index + 1}." if list_element.name == "ol" else "-"
        direct_text, nested_lists = _list_item_content(item)
        line = f"{indent}{prefix}"
        if direct_text:
            line = f"{line} {direct_text}"
        lines.append(line)
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
    return "\n".join(lines)


def _list_item_content(item: Tag) -> tuple[str, list[Tag]]:
    nested_lists: list[Tag] = []
    direct = render_inline_markdown(item.children, nested_lists)
    return direct, nested_lists
