"""Integration tests for full pipeline"""

import pytest
from markdown_ingress.core.fetcher import Fetcher
from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.markdown import MarkdownConverter
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
