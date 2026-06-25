"""Integration tests for full pipeline"""

from markdown_ingress.adapters.extractors.readability_extractor import Extractor
from markdown_ingress.adapters.markdown import markdownify_converter
from markdown_ingress.adapters.markdown.markdownify_converter import MarkdownConverter
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.security import SecurityAnalyzer


def test_full_pipeline_simple(simple_html):
    """Test complete pipeline on simple HTML"""
    # Extract
    extractor = Extractor()
    extraction = extractor.extract(simple_html, "https://test.com")

    # Convert to markdown
    converter = MarkdownConverter()
    markdown = converter.convert(extraction.html)

    # Hash
    hasher = Hasher()
    content_hash = hasher.hash_content(markdown)

    # Security check
    analyzer = SecurityAnalyzer()
    security = analyzer.analyze(extraction.text_content, extraction.removed_hidden > 0)

    # Assertions
    assert len(markdown) > 0
    assert "# Main Heading" in markdown
    assert content_hash.startswith("sha256:")
    assert security.score < 0.3


def test_full_pipeline_with_injection(html_with_injection):
    """Test pipeline detects injection in real HTML"""
    extractor = Extractor()
    extraction = extractor.extract(html_with_injection, "https://test.com")

    converter = MarkdownConverter()
    markdown = converter.convert(extraction.html)

    analyzer = SecurityAnalyzer(strict=True)
    security = analyzer.analyze(extraction.text_content, extraction.removed_hidden > 0)

    # Should detect injection
    assert security.score > 0.5
    assert len(security.pattern_matches) > 0

    # Markdown should still be generated
    assert len(markdown) > 0


def test_determinism_same_input(simple_html):
    """Test that same input produces identical output"""
    extractor = Extractor()
    converter = MarkdownConverter()
    hasher = Hasher()

    # Run twice
    extraction1 = extractor.extract(simple_html, "https://test.com")
    markdown1 = converter.convert(extraction1.html)
    hash1 = hasher.hash_content(markdown1)

    extraction2 = extractor.extract(simple_html, "https://test.com")
    markdown2 = converter.convert(extraction2.html)
    hash2 = hasher.hash_content(markdown2)

    # Should be identical
    assert markdown1 == markdown2
    assert hash1 == hash2


def test_extractor_sanitizes_control_characters_before_readability():
    extractor = Extractor()
    html = (
        "<html><body><article><h1>Hello\x00</h1>"
        "<p>Body\x01 text with control chars.</p></article></body></html>"
    )

    extraction = extractor.extract(html, "https://test.com")

    assert "\x00" not in extraction.html
    assert "\x01" not in extraction.html
    assert "Hello" in extraction.text_content


def test_markdown_converter_preserves_blank_lines_inside_code_blocks():
    converter = MarkdownConverter()
    html = "<pre>line1\n\n\nline2</pre>"

    markdown = converter.convert(html)

    assert "line1\n\n\nline2" in markdown


def test_markdown_converter_ignores_non_string_href_attributes(monkeypatch):
    class FakeLink:
        def get(self, name):
            assert name == "href"
            return ["https://example.test/?utm_source=x"]

        def __setitem__(self, name, value):
            raise AssertionError("non-string href should not be normalized or reassigned")

    class FakeSoup:
        def find_all(self, name, *args, **kwargs):
            if name == "a":
                return [FakeLink()]
            return []

        def __str__(self):
            return '<a href="https://example.test/?utm_source=x">example</a>'

    monkeypatch.setattr(
        markdownify_converter,
        "BeautifulSoup",
        lambda html, parser: FakeSoup(),
    )

    prepared, placeholders = MarkdownConverter()._prepare_html("<a>example</a>")

    assert prepared == '<a href="https://example.test/?utm_source=x">example</a>'
    assert placeholders == {}


def test_markdown_converter_removes_obfuscated_dangerous_links():
    converter = MarkdownConverter()
    html = """
    <p>
        <a href="jav\u200bascript:alert(1)">Hidden JS</a>
        <a href="\uff4aavascript:alert(1)">Fullwidth JS</a>
        <a href="vb\u200bscript:msgbox(1)">Hidden VB</a>
        <a href="da\u200bta:text/html,<script>alert(1)</script>">Hidden data</a>
        <a href="https://example.test/?utm_source=x&ok=1">Safe</a>
    </p>
    """

    markdown = converter.convert(html)

    assert "[Hidden JS]" not in markdown
    assert "[Fullwidth JS]" not in markdown
    assert "[Hidden VB]" not in markdown
    assert "[Hidden data]" not in markdown
    assert "javascript:" not in markdown.lower()
    assert "vbscript:" not in markdown.lower()
    assert "data:text/html" not in markdown.lower()
    assert "[Safe](https://example.test/?ok=1)" in markdown


def test_markdown_converter_preserves_multilevel_headings():
    """ATX headings h2-h6 must keep their hash run intact, not split into '# #'."""
    converter = MarkdownConverter()
    html = "<article><h1>One</h1><h2>Two</h2><h3>Three</h3><h6>Six</h6></article>"

    markdown = converter.convert(html)

    assert "# One" in markdown
    assert "## Two" in markdown
    assert "### Three" in markdown
    assert "###### Six" in markdown
    assert "# # " not in markdown


def test_markdown_converter_adds_space_to_spaceless_headings():
    """A heading whose hashes touch the text gets a single normalizing space."""
    converter = MarkdownConverter()

    assert converter._clean_markdown("##NoSpace") == "## NoSpace\n"
    assert converter._clean_markdown("######Deep") == "###### Deep\n"


def test_markdown_converter_code_block_in_list_starts_on_own_line():
    """A <pre> nested in an <li> must not glue its fence to the item text.

    Regression: the code placeholder was restored inline, producing the
    invalid "- Item```\\ncode\\n```" instead of a fence on its own line.
    """
    converter = MarkdownConverter()

    markdown = converter.convert("<ul><li>Item<pre><code>code here</code></pre></li></ul>")

    assert "- Item```" not in markdown
    assert "- Item\n\n```\ncode here\n```" in markdown


def test_markdown_converter_table_in_list_starts_on_own_line():
    """A <table> nested in an <li> must start on its own line, not glued."""
    converter = MarkdownConverter()

    markdown = converter.convert(
        "<ul><li>Row<table><tr><th>A</th></tr><tr><td>1</td></tr></table></li></ul>"
    )

    assert "- Row|" not in markdown
    assert "- Row\n\n| A |" in markdown


def test_markdown_converter_standalone_code_block_unchanged():
    """Block-level code already on its own line keeps its original layout."""
    converter = MarkdownConverter()

    markdown = converter.convert('<pre><code class="language-python">x = 1</code></pre>')

    assert markdown.startswith("```python\nx = 1\n```")
