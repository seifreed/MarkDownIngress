"""
Structured extraction helpers for block-level and chunk-level outputs.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from bs4 import BeautifulSoup, Tag

from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.structured_chunks import ChunkBuilder as ChunkBuilder
from markdown_ingress.core.structured_chunks import (
    register_token_estimator_factory as register_token_estimator_factory,
)
from markdown_ingress.core.structured_metadata import (
    CODE_LANGUAGE_CLASS_PREFIXES as CODE_LANGUAGE_CLASS_PREFIXES,
)
from markdown_ingress.core.structured_metadata import build_block_metadata
from markdown_ingress.core.structured_rendering import (
    render_block_text,
    render_code_fence,
    render_markdown_block,
    render_markdown_table,
)
from markdown_ingress.models import DocumentChunk, StructuredBlock

__all__ = [
    "CODE_LANGUAGE_CLASS_PREFIXES",
    "ChunkBuilder",
    "HTMLStructureExtractor",
    "blocks_to_dicts",
    "chunks_to_dicts",
    "register_token_estimator_factory",
    "render_code_fence",
    "render_markdown_table",
]


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
            markdown = render_markdown_block(element)
            text = render_block_text(element)
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
