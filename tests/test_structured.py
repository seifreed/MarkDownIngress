from markdown_ingress.core.structured import (
    ChunkBuilder,
    HTMLStructureExtractor,
    blocks_to_dicts,
    chunks_to_dicts,
    register_token_estimator_factory,
    render_code_fence,
    render_markdown_table,
)


class _TokenEstimator:
    def estimate(self, text: str) -> int:
        return len(text.split())

    def estimate_savings(self, original_html: str, markdown: str) -> dict[str, object]:
        return {
            "original_tokens": len(original_html.split()),
            "markdown_tokens": len(markdown.split()),
        }


def _token_estimator_factory() -> _TokenEstimator:
    return _TokenEstimator()


register_token_estimator_factory(_token_estimator_factory)

HTML = """
<html>
  <body>
    <article>
      <h1>Guide</h1>
      <p>Intro paragraph.</p>
      <ol>
        <li>First</li>
        <li>Second</li>
      </ol>
      <pre><code>plain code</code></pre>
      <pre><code class="language-python">print("hi")</code></pre>
      <table>
        <tr><th>A</th><th>B</th></tr>
        <tr><td>1</td><td>2</td></tr>
      </table>
      <blockquote>Quoted text</blockquote>
      <p> </p>
    </article>
  </body>
</html>
"""


def test_render_helpers_cover_empty_and_normal_cases():
    assert render_markdown_table([]) == ""
    assert render_code_fence("x", None).startswith("```")
    assert "| A | B |" in render_markdown_table([["A", "B"], ["1", "2"]])
    assert r"\|" in render_markdown_table([["A|B", r"C\D"]])


def test_html_structure_extractor_covers_lists_code_tables_and_empty_paragraphs():
    extractor = HTMLStructureExtractor()
    blocks = extractor.extract(HTML)

    block_types = [block.block_type for block in blocks]
    assert "heading" in block_types
    assert "list" in block_types
    assert "code" in block_types
    assert "table" in block_types
    assert "quote" in block_types
    assert block_types.count("paragraph") == 1
    assert any(
        block.metadata.get("language") is None for block in blocks if block.block_type == "code"
    )
    assert any(
        block.metadata.get("language") == "python" for block in blocks if block.block_type == "code"
    )


def test_html_structure_extractor_skips_nested_blocks_inside_aggregate_containers():
    extractor = HTMLStructureExtractor()
    html = """
    <html><body><article>
      <blockquote><p>Quoted text</p></blockquote>
      <ul><li><p>List item</p></li></ul>
      <table>
        <tr><td><p>Cell text</p></td></tr>
      </table>
    </article></body></html>
    """

    blocks = extractor.extract(html)

    block_types = [block.block_type for block in blocks]
    assert block_types.count("quote") == 1
    assert block_types.count("list") == 1
    assert block_types.count("table") == 1
    assert "paragraph" not in block_types
    assert any(block.block_type == "quote" and "Quoted text" in block.text for block in blocks)
    assert any(block.block_type == "list" and "List item" in block.text for block in blocks)
    assert any(block.block_type == "table" and "Cell text" in block.text for block in blocks)


def test_html_structure_extractor_preserves_code_indentation_in_text():
    html = """
    <html><body><article>
      <pre><code>    x = 1
        y = 2
</code></pre>
    </article></body></html>
    """

    blocks = HTMLStructureExtractor().extract(html)

    assert len(blocks) == 1
    assert blocks[0].block_type == "code"
    assert blocks[0].text.startswith("    x = 1")
    assert "        y = 2" in blocks[0].text
    assert "    x = 1" in blocks[0].markdown


def test_html_structure_extractor_detects_common_code_language_prefixes():
    html = """
    <html><body><article>
      <pre class="language-rust"><code>fn main() {}</code></pre>
      <pre><code class="lang-python">print("hi")</code></pre>
      <pre><code class="highlight-javascript">const x = 1;</code></pre>
    </article></body></html>
    """

    blocks = HTMLStructureExtractor().extract(html)

    assert [block.metadata["language"] for block in blocks] == [
        "rust",
        "python",
        "javascript",
    ]
    assert blocks[0].markdown.startswith("```rust")
    assert blocks[1].markdown.startswith("```python")
    assert blocks[2].markdown.startswith("```javascript")


def test_html_structure_extractor_preserves_nested_unordered_list_hierarchy():
    html = """
    <html><body><article>
      <ul>
        <li>Parent<ul><li>Child</li></ul></li>
      </ul>
    </article></body></html>
    """

    blocks = HTMLStructureExtractor().extract(html)

    assert len(blocks) == 1
    assert blocks[0].block_type == "list"
    assert blocks[0].markdown == "- Parent\n  - Child\n"
    assert blocks[0].text == "- Parent\n  - Child"
    assert "Parent Child" not in blocks[0].text


def test_html_structure_extractor_preserves_nested_list_hierarchy_inside_wrappers():
    html = """
    <html><body><article>
      <ul>
        <li><div>Parent<ul><li>Child</li></ul></div></li>
      </ul>
    </article></body></html>
    """

    blocks = HTMLStructureExtractor().extract(html)

    assert len(blocks) == 1
    assert blocks[0].markdown == "- Parent\n  - Child\n"
    assert blocks[0].text == "- Parent\n  - Child"
    assert "Parent Child" not in blocks[0].text


def test_html_structure_extractor_deep_nested_lists_do_not_crash():
    depth = 1200
    html = (
        "<html><body><article><ul>"
        + "".join("<li>Item<ul>" for _ in range(depth))
        + "".join("</ul></li>" for _ in range(depth))
        + "</ul></article></body></html>"
    )

    blocks = HTMLStructureExtractor().extract(html)

    assert len(blocks) == 1
    assert blocks[0].block_type == "list"
    assert "- Item" in blocks[0].text


def test_html_structure_extractor_deep_wrapped_list_text_does_not_crash():
    depth = 1200
    html = (
        "<html><body><article><ul><li>"
        + ("<span>" * depth)
        + "Item"
        + ("</span>" * depth)
        + "</li></ul></article></body></html>"
    )

    blocks = HTMLStructureExtractor().extract(html)

    assert len(blocks) == 1
    assert blocks[0].text == "- Item"


def test_block_markdown_preserves_inline_links_and_emphasis():
    """Block/chunk markdown keeps inline links, bold and italic (not plain text)."""
    html = (
        "<html><body><article>"
        "<h2><em>Intro</em> Section</h2>"
        '<p>Visit <a href="https://example.com/doc">the docs</a> for '
        "<strong>important</strong> notes.</p>"
        '<ul><li>See <a href="https://api.example.com">the API</a></li></ul>'
        "</article></body></html>"
    )

    blocks = HTMLStructureExtractor().extract(html)
    by_type = {b.block_type: b.markdown for b in blocks}

    assert by_type["heading"].strip() == "## *Intro* Section"
    assert by_type["paragraph"].strip() == (
        "Visit [the docs](https://example.com/doc) for **important** notes."
    )
    assert by_type["list"].strip() == "- See [the API](https://api.example.com)"
    # .text stays plain (no markdown syntax)
    para = next(b for b in blocks if b.block_type == "paragraph")
    assert para.text == "Visit the docs for important notes."


def test_block_markdown_preserves_nested_blockquote_depth():
    """Nested blockquotes gain a '> ' per level, like the main converter."""
    html = (
        "<html><body><article>"
        "<blockquote><p>Outer</p><blockquote><p>Inner</p></blockquote></blockquote>"
        "</article></body></html>"
    )

    blocks = HTMLStructureExtractor().extract(html)

    assert blocks[0].markdown.strip() == "> Outer\n>\n> > Inner"


def test_html_structure_extractor_deep_blockquotes_do_not_crash():
    html = (
        "<html><body><article>"
        + "<blockquote>" * 1200
        + "<p>deep</p>"
        + "</blockquote>" * 1200
        + "</article></body></html>"
    )

    blocks = HTMLStructureExtractor().extract(html)

    assert len(blocks) == 1
    assert "deep" in blocks[0].markdown


def test_block_markdown_drops_dangerous_link_scheme_keeping_text():
    """A javascript: href is stripped like the main converter, text preserved."""
    html = (
        "<html><body><article>"
        '<p>Click <a href="javascript:alert(1)">here</a></p>'
        "</article></body></html>"
    )

    blocks = HTMLStructureExtractor().extract(html)

    assert blocks[0].markdown.strip() == "Click here"
    assert "javascript:" not in blocks[0].markdown


def test_html_structure_extractor_preserves_nested_ordered_list_hierarchy():
    html = """
    <html><body><article>
      <ol>
        <li>First
          <ol>
            <li>Nested one</li>
            <li>Nested two</li>
          </ol>
        </li>
      </ol>
    </article></body></html>
    """

    blocks = HTMLStructureExtractor().extract(html)

    assert len(blocks) == 1
    # Nested ordered items indent 3 spaces to align under "1. " (CommonMark),
    # matching the main markdown converter.
    assert blocks[0].markdown == "1. First\n   1. Nested one\n   2. Nested two\n"
    assert blocks[0].text == "1. First\n   1. Nested one\n   2. Nested two"


def test_chunk_builder_covers_none_heading_and_size_strategies():
    extractor = HTMLStructureExtractor()
    blocks = extractor.extract(HTML)
    builder = ChunkBuilder()

    assert builder.build([], strategy="heading") == []
    assert builder.build(blocks, strategy="none") == []

    heading_chunks = builder.build(blocks, strategy="heading")
    size_chunks = builder.build(blocks, strategy="size", chunk_size=20, chunk_overlap=5)

    assert len(heading_chunks) >= 1
    assert len(size_chunks) >= 2
    # Chunks should be contiguous (with inter-chunk separator accounted for)
    assert size_chunks[1].char_start >= size_chunks[0].char_end
    assert size_chunks[0].metadata["emitted_char_start"] == 0
    assert size_chunks[0].metadata["emitted_char_end"] == len(size_chunks[0].text)
    # After overlap is applied, emitted_char_start/end point to the non-overlapped
    # portion within the (now overlap-prefixed) text.
    overlap_prefix_len = size_chunks[1].metadata["overlap_prefix_len"]
    original_len = (
        size_chunks[1].metadata["original_char_end"]
        - size_chunks[1].metadata["original_char_start"]
    )
    assert size_chunks[1].metadata["emitted_char_start"] == overlap_prefix_len
    assert size_chunks[1].metadata["emitted_char_end"] == overlap_prefix_len + original_len
    assert size_chunks[1].char_start == size_chunks[1].metadata["original_char_start"]
    assert size_chunks[1].char_end == size_chunks[1].metadata["original_char_end"]


def test_chunk_builder_preserves_nested_list_structure_in_chunk_text():
    html = """
    <html><body><article>
      <ul>
        <li>Parent<ul><li>Child</li></ul></li>
      </ul>
    </article></body></html>
    """

    blocks = HTMLStructureExtractor().extract(html)
    chunks = ChunkBuilder().build(blocks, strategy="size", chunk_size=100, chunk_overlap=0)

    assert len(chunks) == 1
    assert chunks[0].text == "- Parent\n  - Child"
    assert "Parent Child" not in chunks[0].text


def test_chunk_builder_preserves_wrapped_nested_list_structure_in_chunk_text():
    html = """
    <html><body><article>
      <ul>
        <li><div>Parent<ul><li>Child</li></ul></div></li>
      </ul>
    </article></body></html>
    """

    blocks = HTMLStructureExtractor().extract(html)
    chunks = ChunkBuilder().build(blocks, strategy="size", chunk_size=100, chunk_overlap=0)

    assert len(chunks) == 1
    assert chunks[0].text == "- Parent\n  - Child"
    assert "Parent Child" not in chunks[0].text


def test_chunk_builder_heading_strategy_respects_chunk_size_for_multiple_blocks():
    html = (
        "<html><body><h1>Title</h1>"
        + "".join(f"<p>{i:02d}</p>" for i in range(20))
        + "</body></html>"
    )

    blocks = HTMLStructureExtractor().extract(html)
    chunks = ChunkBuilder().build(blocks, strategy="heading", chunk_size=20, chunk_overlap=0)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 20 for chunk in chunks)


def test_chunk_overlap_does_not_compound_across_chunks():
    """Bug fix: overlap from chunk[i-2] must not leak into chunk[i]'s overlap."""
    from markdown_ingress.models import StructuredBlock

    # Create 3 blocks with very short text (2 chars each) and large overlap (4)
    # so the overlap exceeds the original text length, forcing the edge case
    # where WITHOUT the fix, chunk[2]'s overlap would bleed into chunk[1]'s
    # prepended separator and chunk[0]'s text.
    blocks = [
        StructuredBlock(
            block_type="paragraph", text="AA", markdown="AA\n", ordinal=0, structural_hash="h0"
        ),
        StructuredBlock(
            block_type="paragraph", text="BB", markdown="BB\n", ordinal=1, structural_hash="h1"
        ),
        StructuredBlock(
            block_type="paragraph", text="CC", markdown="CC\n", ordinal=2, structural_hash="h2"
        ),
    ]
    builder = ChunkBuilder()
    chunks = builder.build(blocks, strategy="size", chunk_size=5, chunk_overlap=4)
    assert len(chunks) >= 3
    # Extract the overlap portion of chunk[2] (text before the separator)
    separator = "\n\n---\n\n"
    chunk2_text = chunks[2].text
    sep_pos = chunk2_text.find(separator)
    assert sep_pos >= 0, f"Expected separator in chunk 2: {chunk2_text!r}"
    overlap_portion = chunk2_text[:sep_pos]
    # The overlap must come from chunk[1]'s ORIGINAL text "BB" only.
    # Without the fix, it would contain characters from the separator
    # that was prepended to chunk[1] (e.g. "\n\n" or "---" fragments).
    assert (
        overlap_portion == "BB"
    ), f"Chunk 2 overlap should be 'BB' (from original chunk 1), got {overlap_portion!r}"


def test_chunk_builder_counts_empty_text_blocks_using_markdown_length():
    from markdown_ingress.models import StructuredBlock

    blocks = [
        StructuredBlock(
            block_type="code", text="", markdown="ABCD", ordinal=0, structural_hash="h0"
        ),
        StructuredBlock(
            block_type="paragraph", text="EF", markdown="EF", ordinal=1, structural_hash="h1"
        ),
    ]
    builder = ChunkBuilder()

    chunks = builder.build(blocks, strategy="size", chunk_size=5, chunk_overlap=0)

    assert len(chunks) == 2
    assert chunks[0].text == "ABCD"
    assert chunks[0].markdown.startswith("ABCD")
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == 4
    assert chunks[0].token_estimate > 0
    assert chunks[1].text == "EF"
    assert chunks[1].markdown.startswith("EF")
    assert chunks[1].char_start == 6
    assert chunks[1].char_end == 8


def test_chunk_builder_offsets_include_inter_group_separators():
    from markdown_ingress.models import StructuredBlock

    blocks = [
        StructuredBlock(
            block_type="paragraph", text="aaaa", markdown="aaaa\n", ordinal=0, structural_hash="h0"
        ),
        StructuredBlock(
            block_type="paragraph", text="bbbb", markdown="bbbb\n", ordinal=1, structural_hash="h1"
        ),
        StructuredBlock(
            block_type="paragraph", text="cccc", markdown="cccc\n", ordinal=2, structural_hash="h2"
        ),
    ]

    chunks = ChunkBuilder().build(blocks, strategy="size", chunk_size=5, chunk_overlap=0)

    assert [(chunk.text, chunk.char_start, chunk.char_end) for chunk in chunks] == [
        ("aaaa", 0, 4),
        ("bbbb", 6, 10),
        ("cccc", 12, 16),
    ]


def test_hasher_normalizes_list_indentation_for_structural_hash():
    from markdown_ingress.core.hashing import Hasher

    hasher = Hasher()

    top_level_a = hasher.hash_structural("- one\n- two\n")
    top_level_b = hasher.hash_structural("  - one\n  - two\n")
    nested = hasher.hash_structural("    - one\n    - two\n")

    assert top_level_a == top_level_b
    assert top_level_a != nested


def test_structured_serializers_return_plain_dicts():
    extractor = HTMLStructureExtractor()
    blocks = extractor.extract(HTML)
    chunks = ChunkBuilder().build(blocks, strategy="heading")

    block_dicts = blocks_to_dicts(blocks)
    chunk_dicts = chunks_to_dicts(chunks)

    assert isinstance(block_dicts, list)
    assert isinstance(chunk_dicts, list)
    assert block_dicts[0]["block_type"] == blocks[0].block_type
    assert chunk_dicts[0]["chunk_id"] == chunks[0].chunk_id


def test_markdown_converter_lang_prefix_detected():
    """lang-* class prefix should be recognized as code language."""
    from markdown_ingress.adapters.markdown.markdownify_converter import MarkdownConverter

    converter = MarkdownConverter()
    html = '<pre><code class="lang-python">x = 1\n</code></pre>'
    result = converter.convert(html)
    assert "```python" in result


def test_markdown_converter_highlight_prefix_detected():
    """highlight-* class prefix should be recognized as code language."""
    from markdown_ingress.adapters.markdown.markdownify_converter import MarkdownConverter

    converter = MarkdownConverter()
    html = '<pre><code class="highlight-javascript">const x = 1;\n</code></pre>'
    result = converter.convert(html)
    assert "```javascript" in result


def test_markdown_converter_language_prefix_still_works():
    """language-* class prefix (original) must continue working after the multi-prefix refactor."""
    from markdown_ingress.adapters.markdown.markdownify_converter import MarkdownConverter

    converter = MarkdownConverter()
    html = '<pre><code class="language-rust">fn main() {}\n</code></pre>'
    result = converter.convert(html)
    assert "```rust" in result
