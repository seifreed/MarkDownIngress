from markdown_ingress.core.structured import (
    ChunkBuilder,
    HTMLStructureExtractor,
    blocks_to_dicts,
    chunks_to_dicts,
    render_code_fence,
    render_markdown_table,
)


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
    assert any(block.metadata.get("language") is None for block in blocks if block.block_type == "code")
    assert any(block.metadata.get("language") == "python" for block in blocks if block.block_type == "code")


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
    assert size_chunks[1].metadata["emitted_char_start"] == 0
    assert size_chunks[1].metadata["emitted_char_end"] == len(size_chunks[1].text)
    assert size_chunks[1].metadata["original_char_start"] == size_chunks[1].char_start
    assert size_chunks[1].metadata["original_char_end"] == size_chunks[1].char_end


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
