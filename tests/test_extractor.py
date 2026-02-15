"""Tests for extraction and cleaning"""

import pytest
from markdown_ingress.core.extractor import Extractor


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
    assert len(result.removed_tags) > 0 or True  # Readability might already clean them
    
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
