"""Chunk construction for structured document blocks."""

from __future__ import annotations

from collections.abc import Callable

from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.interfaces import ITokenEstimator
from markdown_ingress.models import DocumentChunk, StructuredBlock

_token_estimator_factory: Callable[[], ITokenEstimator] | None = None


def register_token_estimator_factory(fn: Callable[[], ITokenEstimator]) -> None:
    global _token_estimator_factory
    _token_estimator_factory = fn


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
        """Build chunks according to the requested strategy with overlap support."""
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
                cursor += 2
            char_start = cursor
            char_end = cursor + len(text)

            chunk = DocumentChunk(
                chunk_id=f"chunk-{index + 1}",
                text=text,
                markdown=markdown,
                block_ordinals=[block.ordinal for block in group],
                structural_hash=structural_hash,
                token_estimate=self.token_estimator.estimate(text),
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

        if chunk_overlap > 0 and len(chunks) > 1:
            self._apply_overlap(chunks, chunk_overlap)

        return chunks

    def _apply_overlap(self, chunks: list[DocumentChunk], chunk_overlap: int) -> None:
        original_texts = [chunk.text for chunk in chunks]
        for i in range(1, len(chunks)):
            prev_chunk = chunks[i - 1]
            curr_chunk = chunks[i]

            prev_original = original_texts[i - 1]
            overlap_text = (
                prev_original[-chunk_overlap:]
                if len(prev_original) > chunk_overlap
                else prev_original
            )

            overlap_prefix_len = len(overlap_text) + len("\n\n---\n\n")
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
            added_len = block_len + (2 if current else 0)
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
