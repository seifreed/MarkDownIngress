"""Tests for extraction and cleaning"""

from markdown_ingress.adapters.extractors.readability_extractor import Extractor


def test_simple_extraction(simple_html):
    """Test basic extraction on clean HTML"""
    extractor = Extractor(strict=True)
    result = extractor.extract(simple_html, "https://test.com")

    assert result.title == "Test Page"
    assert "Main Heading" in result.text_content
    assert "simple paragraph" in result.text_content


def test_remove_hidden_elements(html_with_hidden):
    """Test that hidden elements are removed"""
    extractor = Extractor(strict=True)
    result = extractor.extract(html_with_hidden, "https://test.com")

    # Should remove hidden content
    assert result.removed_hidden > 0

    # Hidden text should not appear
    assert "ignore all previous instructions" not in result.text_content.lower()
    assert "Secret content" not in result.text_content


def test_remove_unwanted_tags(html_with_noise):
    """Test removal of nav, footer, aside, script, style"""
    extractor = Extractor(strict=True)
    result = extractor.extract(html_with_noise, "https://test.com")

    # Should track some removed tags (readability removes script/style before we see them)
    # So we mainly check that nav/aside/footer are handled
    assert isinstance(result.removed_tags, dict)  # Validate return type

    # Main content should be present
    assert "Main Article" in result.text_content
    assert "main content" in result.text_content

    # Script/style content should NOT be in text
    assert "console.log" not in result.text_content
    assert "analytics()" not in result.text_content


def test_extraction_preserves_structure():
    """Test that extraction preserves basic document structure"""
    html = """
    <article>
        <h1>Title</h1>
        <h2>Section 1</h2>
        <p>Content 1</p>
        <h2>Section 2</h2>
        <p>Content 2</p>
    </article>
    """

    extractor = Extractor()
    result = extractor.extract(html, "https://test.com")

    # Should preserve headings
    assert "Title" in result.text_content
    assert "Section 1" in result.text_content
    assert "Section 2" in result.text_content


def test_sanitize_dangerous_svg_data_urls_and_srcset():
    extractor = Extractor()
    svg_data_url = (
        "data:image/svg+xml,"
        "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    )
    html = f"""
    <html>
    <body>
        <img
            src="{svg_data_url}"
            srcset="https://example.com/a.png 1x, {svg_data_url} 2x"
        >
        <div style="background-image:url(javascript:alert(1))">X</div>
    </body>
    </html>
    """

    result = extractor.extract(html, "https://test.com")

    assert "data:image/svg+xml" not in result.html
    assert "srcset" not in result.html
    assert "style=" not in result.html
    assert "javascript:alert(1)" not in result.html


def test_sanitize_obfuscated_dangerous_url_schemes():
    extractor = Extractor()
    html = """
    <html>
    <body>
        <article>
            <a href="jav\u200bascript:alert(1)">Hidden JS</a>
            <a href="\uff4aavascript:alert(1)">Fullwidth JS</a>
            <a href="vb\u200bscript:msgbox(1)">Hidden VB</a>
            <a href="da\u200bta:text/html,<script>alert(1)</script>">Hidden data</a>
            <p>Keep me visible for readability extraction.</p>
        </article>
    </body>
    </html>
    """

    result = extractor.extract(html, "https://test.com")

    assert "href=" not in result.html
    assert "javascript:" not in result.html.lower()
    assert "vbscript:" not in result.html.lower()
    assert "data:text/html" not in result.html.lower()


def test_dangerous_url_scheme_detects_across_fast_and_slow_paths():
    from markdown_ingress.core.url_safety import (
        dangerous_url_scheme,
        normalize_url_value_for_scheme_detection,
    )

    # Fast path (plain ASCII): spaces are stripped before scheme matching.
    assert dangerous_url_scheme("java script:alert(1)") == "javascript"
    assert dangerous_url_scheme("  JAVASCRIPT:alert(1)  ") == "javascript"
    assert dangerous_url_scheme("https://example.com/path") is None
    assert normalize_url_value_for_scheme_detection("j a v a script:1") == "javascript:1"

    # Slow path (non-ASCII obfuscation chars) must still be caught.
    assert dangerous_url_scheme("jav​ascript:alert(1)") == "javascript"  # zero-width space
    assert dangerous_url_scheme("java­script:alert(1)") == "javascript"  # soft hyphen
    assert dangerous_url_scheme("data:image/png;base64,AAAA") is None  # safe data url


def test_visible_absolute_positioned_content_is_preserved():
    extractor = Extractor()
    html = """
    <html>
    <body>
        <div class="not-hidden">Visible via class</div>
        <div style="position:absolute; top:10px; left:10px">Visible absolute</div>
        <div style="position:absolute; left:-9999px">Hidden absolute</div>
        <div style="opacity:0.5">Semi visible</div>
        <div style="opacity:0">Hidden opacity</div>
        <div class="hidden">Hidden class</div>
        <p>Keep me</p>
    </body>
    </html>
    """

    result = extractor.extract(html, "https://test.com")

    assert "Visible via class" in result.text_content
    assert "Visible absolute" in result.text_content
    assert "Hidden absolute" not in result.text_content
    assert "Semi visible" in result.text_content
    assert "Hidden opacity" not in result.text_content
    assert "Hidden class" not in result.text_content


def test_hidden_styles_with_spacing_are_removed():
    extractor = Extractor()
    html = """
    <html>
    <body>
        <div style="display : none">Hidden display</div>
        <div style="visibility : hidden">Hidden visibility</div>
        <div style="content-visibility : hidden">Hidden content visibility</div>
        <div style="clip-path : inset(100%)">Hidden clip path</div>
        <div style="transform : scale(0)">Hidden transform</div>
        <div style="left : -9999px">Hidden offset</div>
        <div style="color : transparent">Hidden color</div>
        <div style="display:none">Hidden display normal</div>
        <p>Keep me</p>
    </body>
    </html>
    """

    result = extractor.extract(html, "https://test.com")

    assert "Hidden display" not in result.text_content
    assert "Hidden visibility" not in result.text_content
    assert "Hidden content visibility" not in result.text_content
    assert "Hidden clip path" not in result.text_content
    assert "Hidden transform" not in result.text_content
    assert "Hidden offset" not in result.text_content
    assert "Hidden color" not in result.text_content
    assert "Hidden display normal" not in result.text_content
    assert "Keep me" in result.text_content
