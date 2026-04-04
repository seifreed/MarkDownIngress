"""
Structured extraction helpers for block-level and chunk-level outputs.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from bs4 import BeautifulSoup, NavigableString, Tag

from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.tokens import TokenEstimator
from markdown_ingress.models import DocumentChunk, StructuredBlock


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


def render_markdown_table(rows: list[list[str]]) -> str:
    """Render a markdown table from normalized rows."""
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    header = normalized_rows[0]
    divider = ["---"] * width
    body = normalized_rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(divider) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines) + "\n"


class HTMLStructureExtractor:
    """Extract structured blocks from cleaned HTML."""

    BLOCK_TAGS = {
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
            if element.parent and isinstance(element.parent, Tag) and element.parent.name in {"pre", "table"}:
                continue

            block_type, level = self.BLOCK_TAGS[element.name]
            markdown = self._to_markdown_block(element)
            text = self._to_text(element)
            if not text.strip() and block_type not in {"table", "code"}:
                continue

            block = StructuredBlock(
                block_type=block_type,
                text=text.strip(),
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
            lines = [line.strip() for line in self._to_text(element).splitlines() if line.strip()]
            return "\n".join(f"> {line}" for line in lines)
        if element.name == "pre":
            code = element.get_text("\n", strip=False)
            language = self._detect_code_language(element)
            return render_code_fence(code, language)
        if element.name == "table":
            rows = []
            for tr in element.find_all("tr"):
                row = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
                if row:
                    rows.append(row)
            return render_markdown_table(rows)
        if element.name in {"ul", "ol"}:
            ordered = element.name == "ol"
            lines: list[str] = []
            for index, item in enumerate(element.find_all("li", recursive=False), start=1):
                prefix = f"{index}." if ordered else "-"
                lines.append(f"{prefix} {item.get_text(' ', strip=True)}")
            return "\n".join(lines)
        return self._to_text(element).strip()

    def _to_text(self, element: Tag) -> str:
        return element.get_text("\n" if element.name == "pre" else " ", strip=True)

    def _detect_code_language(self, element: Tag) -> str | None:
        code = element.find("code")
        if code is None:
            return None
        classes = code.get("class", [])
        for value in classes:
            if value.startswith("language-"):
                return value.removeprefix("language-")
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


class ChunkBuilder:
    """Create stable chunks from structured blocks."""

    def __init__(self, hasher: Hasher | None = None, token_estimator: TokenEstimator | None = None):
        self.hasher = hasher or Hasher()
        self.token_estimator = token_estimator or TokenEstimator()

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
        _CHUNK_SEPARATOR = "\n\n"

        for index, group in enumerate(groups):
            # Build text without strip to preserve character offsets
            # Filter empty blocks and join with separator
            block_texts = [block.text for block in group if block.text]
            text = "\n\n".join(block_texts)
            markdown = "\n\n".join(block.markdown.strip() for block in group).strip() + "\n"
            structural_source = "\n".join(block.structural_hash for block in group)
            structural_hash = self.hasher.hash_content(structural_source)

            # Account for inter-chunk separator in char offsets
            if index > 0:
                cursor += len(_CHUNK_SEPARATOR)
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
            for i in range(1, len(chunks)):
                prev_chunk = chunks[i - 1]
                curr_chunk = chunks[i]

                # Get the last `chunk_overlap` characters from previous chunk
                overlap_text = prev_chunk.text[-chunk_overlap:] if len(prev_chunk.text) > chunk_overlap else prev_chunk.text

                # Prepend overlap to current chunk's text (for retrieval context)
                # Note: We keep the original markdown - overlap is for retrieval context only
                # Track the overlap prefix length for downstream processing
                overlap_prefix_len = len(overlap_text) + len("\n\n---\n\n")
                # Store original offsets before modification
                curr_chunk.metadata["original_char_start"] = curr_chunk.char_start
                curr_chunk.metadata["original_char_end"] = curr_chunk.char_end
                curr_chunk.text = overlap_text + "\n\n---\n\n" + curr_chunk.text
                curr_chunk.metadata["overlap_from"] = prev_chunk.chunk_id
                curr_chunk.metadata["overlap_prefix_len"] = overlap_prefix_len
                curr_chunk.metadata["emitted_char_start"] = 0
                curr_chunk.metadata["emitted_char_end"] = len(curr_chunk.text)

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
            for block in blocks:
                if block.block_type == "heading" and current:
                    groups.append(current)
                    current = [block]
                else:
                    current.append(block)
            if current:
                groups.append(current)
            return groups

        groups = []
        current = []
        current_len = 0
        for block in blocks:
            block_len = len(block.text) or len(block.markdown)
            if current and current_len + block_len > chunk_size:
                groups.append(current)
                current = [block]
                current_len = block_len
            else:
                current.append(block)
                current_len += block_len
        if current:
            groups.append(current)
        return groups


def blocks_to_dicts(blocks: list[StructuredBlock]) -> list[dict]:
    """Serialize blocks for metadata and API responses."""
    return [block.to_dict() for block in blocks]


def chunks_to_dicts(chunks: list[DocumentChunk]) -> list[dict]:
    """Serialize chunks for metadata and API responses."""
    return [chunk.to_dict() for chunk in chunks]
