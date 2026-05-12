"""
Structured extraction helpers for block-level and chunk-level outputs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from bs4 import BeautifulSoup, NavigableString, PageElement, Tag

from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.interfaces import ITokenEstimator
from markdown_ingress.models import DocumentChunk, StructuredBlock

_token_estimator_factory: Callable[[], ITokenEstimator] | None = None


def register_token_estimator_factory(fn: Callable[[], ITokenEstimator]) -> None:
    global _token_estimator_factory
    _token_estimator_factory = fn


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
    CODE_LANGUAGE_CLASS_PREFIXES = ("language-", "lang-", "highlight-")

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
                metadata=self._build_metadata(element, block_type),
            )
            blocks.append(block)
            ordinal += 1

        return blocks

    def _to_markdown_block(self, element: Tag) -> str:
        if element.name and element.name.startswith("h"):
            level = int(element.name[1])
            return f"{'#' * level} {self._to_text(element).strip()}"
        if element.name == "blockquote":
            blockquote_lines = [
                line.strip() for line in self._to_text(element).splitlines() if line.strip()
            ]
            return "\n".join(f"> {line}" for line in blockquote_lines)
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
        return self._to_text(element).strip()

    def _to_text(self, element: Tag) -> str:
        if element.name == "pre":
            return element.get_text("\n", strip=False).rstrip("\n")
        if element.name in {"ul", "ol"}:
            return self._render_list(element)
        return element.get_text(" ", strip=True)

    def _render_list(self, element: Tag) -> str:
        lines: list[str] = []
        self._append_list_lines(element, lines, depth=0)
        return "\n".join(lines)

    def _append_list_lines(self, element: Tag, lines: list[str], *, depth: int) -> None:
        ordered = element.name == "ol"
        indent = "  " * depth
        for index, item in enumerate(element.find_all("li", recursive=False), start=1):
            prefix = f"{index}." if ordered else "-"
            direct_text, nested_lists = self._list_item_content(item)
            line = f"{indent}{prefix}"
            if direct_text:
                line = f"{line} {direct_text}"
            lines.append(line)
            for nested_list in nested_lists:
                self._append_list_lines(nested_list, lines, depth=depth + 1)

    @staticmethod
    def _list_item_content(item: Tag) -> tuple[str, list[Tag]]:
        parts: list[str] = []
        nested_lists: list[Tag] = []

        def walk(node: PageElement) -> None:
            if isinstance(node, NavigableString):
                value = " ".join(str(node).split())
                if value:
                    parts.append(value)
                return
            if not isinstance(node, Tag):
                return
            if node.name in {"ul", "ol"}:
                nested_lists.append(node)
                return
            for child in node.children:
                walk(child)

        for child in item.children:
            walk(child)
        return " ".join(parts), nested_lists

    def _detect_code_language(self, element: Tag) -> str | None:
        raw_classes: list[str] = []
        for candidate in (element, element.find("code")):
            if candidate is None:
                continue
            classes = candidate.get("class")
            if isinstance(classes, str):
                raw_classes.append(classes)
            else:
                raw_classes.extend(list(classes or []))

        for value in raw_classes:
            for prefix in self.CODE_LANGUAGE_CLASS_PREFIXES:
                if value.startswith(prefix):
                    return value.removeprefix(prefix)
        return None

    def _build_metadata(self, element: Tag, block_type: str) -> dict:
        metadata: dict[str, object] = {"tag": element.name}
        if block_type == "table":
            rows = element.find_all("tr")
            metadata["rows"] = len(rows)
            metadata["columns"] = max(
                (len(row.find_all(["th", "td"])) for row in rows),
                default=0,
            )
        if block_type == "code":
            metadata["language"] = self._detect_code_language(element)
        return metadata

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


class ChunkBuilder:
    """Create stable chunks from structured blocks."""

    def __init__(
        self, hasher: Hasher | None = None, token_estimator: ITokenEstimator | None = None
    ):
        self.hasher = hasher or Hasher()
        if token_estimator is None:
            if _token_estimator_factory is None:
                raise RuntimeError("No token estimator factory registered.")
            token_estimator = _token_estimator_factory()
        self.token_estimator = token_estimator

    @staticmethod
    def _chunk_text_for_block(block: StructuredBlock) -> str:
        """Return the text payload used for chunk offsets and token estimates."""
        if block.text:
            return block.text
        return block.markdown.strip()

    def build(
        self,
        blocks: list[StructuredBlock],
        strategy: str = "none",
        chunk_size: int = 1200,
        chunk_overlap: int = 120,
    ) -> list[DocumentChunk]:
        """Build chunks according to the requested strategy with overlap support.

        Args:
            blocks: List of structured blocks to chunk
            strategy: Chunking strategy ("none", "heading")
            chunk_size: Maximum characters per chunk
            chunk_overlap: Number of characters to overlap between chunks

        Returns:
            List of DocumentChunk objects
        """
        if not blocks or strategy == "none":
            return []
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        if chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be >= 0, got {chunk_overlap}")
        if chunk_overlap >= chunk_size:
            raise ValueError(f"chunk_overlap ({chunk_overlap}) must be < chunk_size ({chunk_size})")

        groups = self._group_blocks(blocks, strategy, chunk_size)
        chunks: list[DocumentChunk] = []
        cursor = 0

        for index, group in enumerate(groups):
            # Preserve structurally significant code/table blocks whose text is
            # empty but whose markdown still carries emitted content.
            block_texts = [text for block in group if (text := self._chunk_text_for_block(block))]
            text = "\n\n".join(block_texts)
            markdown = "\n\n".join(block.markdown.strip() for block in group).strip() + "\n"
            structural_source = "\n".join(block.structural_hash for block in group)
            structural_hash = self.hasher.hash_content(structural_source)

            if index > 0:
                cursor += 2  # inter-chunk "\n\n" separator in the canonical text stream
            char_start = cursor
            char_end = cursor + len(text)

            chunk = DocumentChunk(
                chunk_id=f"chunk-{index + 1}",
                text=text,
                markdown=markdown,
                block_ordinals=[block.ordinal for block in group],
                structural_hash=structural_hash,
                token_estimate=self.token_estimator.estimate(text),  # Use text, not markdown
                char_start=char_start,
                char_end=char_end,
                metadata={
                    "strategy": strategy,
                    "overlap": chunk_overlap if index > 0 else 0,
                    "emitted_char_start": 0,
                    "emitted_char_end": len(text),
                },
            )
            chunks.append(chunk)
            cursor = char_end

        # Apply chunk overlap by prepending text from previous chunk
        if chunk_overlap > 0 and len(chunks) > 1:
            # Snapshot original texts before mutation to prevent overlap compounding:
            # without this, chunk[i]'s overlap would include the overlap already
            # prepended to chunk[i-1], causing cascading contamination.
            original_texts = [chunk.text for chunk in chunks]
            for i in range(1, len(chunks)):
                prev_chunk = chunks[i - 1]
                curr_chunk = chunks[i]

                # Get the last `chunk_overlap` characters from previous chunk's ORIGINAL text
                prev_original = original_texts[i - 1]
                overlap_text = (
                    prev_original[-chunk_overlap:]
                    if len(prev_original) > chunk_overlap
                    else prev_original
                )

                # Prepend overlap to current chunk's text (for retrieval context)
                # Note: We keep the original markdown - overlap is for retrieval context only
                # Track the overlap prefix length for downstream processing
                overlap_prefix_len = len(overlap_text) + len("\n\n---\n\n")
                # Store original offsets and text length before modification
                curr_chunk.metadata["original_char_start"] = curr_chunk.char_start
                curr_chunk.metadata["original_char_end"] = curr_chunk.char_end
                original_text_len = len(curr_chunk.text)
                curr_chunk.text = overlap_text + "\n\n---\n\n" + curr_chunk.text
                curr_chunk.metadata["overlap_from"] = prev_chunk.chunk_id
                curr_chunk.metadata["overlap_prefix_len"] = overlap_prefix_len
                curr_chunk.metadata["text_includes_overlap"] = True
                curr_chunk.metadata["emitted_char_start"] = overlap_prefix_len
                curr_chunk.metadata["emitted_char_end"] = overlap_prefix_len + original_text_len
                curr_chunk.token_estimate = self.token_estimator.estimate(curr_chunk.text)

        return chunks

    def _group_blocks(
        self,
        blocks: list[StructuredBlock],
        strategy: str,
        chunk_size: int,
    ) -> list[list[StructuredBlock]]:
        if strategy == "heading":
            groups: list[list[StructuredBlock]] = []
            current: list[StructuredBlock] = []
            current_len = 0
            for block in blocks:
                block_len = len(block.text) if block.text else len(block.markdown)
                added_len = block_len + (2 if current else 0)
                if current and (
                    block.block_type == "heading" or current_len + added_len > chunk_size
                ):
                    groups.append(current)
                    current = [block]
                    current_len = block_len
                else:
                    current.append(block)
                    current_len += added_len
            if current:
                groups.append(current)
            return groups

        groups = []
        current = []
        current_len = 0
        for block in blocks:
            block_len = len(block.text) if block.text else len(block.markdown)
            added_len = block_len + (2 if current else 0)  # account for "\n\n" join separator
            if current and current_len + added_len > chunk_size:
                groups.append(current)
                current = [block]
                current_len = block_len
            else:
                current.append(block)
                current_len += added_len
        if current:
            groups.append(current)
        return groups


def blocks_to_dicts(blocks: list[StructuredBlock]) -> list[dict]:
    """Serialize blocks for metadata and API responses."""
    return [block.to_dict() for block in blocks]


def chunks_to_dicts(chunks: list[DocumentChunk]) -> list[dict]:
    """Serialize chunks for metadata and API responses."""
    return [chunk.to_dict() for chunk in chunks]
