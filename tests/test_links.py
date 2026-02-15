"""
Tests for link extraction and analysis
"""

import pytest
from markdown_ingress.core.link_analyzer import LinkAnalyzer


def test_extract_internal_links():
    """Test extracting internal links"""
    html = """
    <html>
    <body>
        <a href="/page1">Page 1</a>
        <a href="/page2">Page 2</a>
        <a href="https://example.com/page3">Page 3</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert len(result["internal"]) == 3
    assert "https://example.com/page1" in result["internal"]
    assert "https://example.com/page2" in result["internal"]
    assert "https://example.com/page3" in result["internal"]


def test_extract_external_links():
    """Test extracting external links"""
    html = """
    <html>
    <body>
        <a href="https://google.com">Google</a>
        <a href="https://github.com/repo">GitHub</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert len(result["external"]) == 2
    assert "https://google.com" in result["external"]
    assert "https://github.com/repo" in result["external"]


def test_extract_anchor_links():
    """Test extracting anchor links"""
    html = """
    <html>
    <body>
        <a href="#section1">Section 1</a>
        <a href="#section2">Section 2</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert len(result["anchors"]) == 2
    assert "#section1" in result["anchors"]
    assert "#section2" in result["anchors"]


def test_skip_mailto_links():
    """Test that mailto links are skipped"""
    html = """
    <html>
    <body>
        <a href="mailto:test@example.com">Email</a>
        <a href="tel:+1234567890">Phone</a>
        <a href="javascript:void(0)">JS</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert result["total"] == 0
    assert len(result["internal"]) == 0
    assert len(result["external"]) == 0


def test_count_by_domain():
    """Test counting links by domain"""
    html = """
    <html>
    <body>
        <a href="https://google.com/1">Google 1</a>
        <a href="https://google.com/2">Google 2</a>
        <a href="https://github.com">GitHub</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert result["by_domain"]["google.com"] == 2
    assert result["by_domain"]["github.com"] == 1


def test_deduplicate_links():
    """Test that duplicate links are deduplicated"""
    html = """
    <html>
    <body>
        <a href="/page1">Page 1</a>
        <a href="/page1">Page 1 Again</a>
        <a href="https://google.com">Google</a>
        <a href="https://google.com">Google Again</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert len(result["internal"]) == 1
    assert len(result["external"]) == 1


def test_total_count():
    """Test total link count"""
    html = """
    <html>
    <body>
        <a href="/page1">Internal</a>
        <a href="https://google.com">External</a>
        <a href="#anchor">Anchor</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert result["total"] == 3


def test_empty_html():
    """Test with no links"""
    html = """
    <html>
    <body>
        <p>No links here</p>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert result["total"] == 0
    assert len(result["internal"]) == 0
    assert len(result["external"]) == 0
    assert len(result["anchors"]) == 0
    assert len(result["by_domain"]) == 0


def test_relative_urls():
    """Test resolving relative URLs"""
    html = """
    <html>
    <body>
        <a href="page.html">Relative</a>
        <a href="./about.html">Dot Relative</a>
        <a href="../parent.html">Parent</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com/dir/page")
    
    assert len(result["internal"]) == 3


def test_subdomain_as_external():
    """Test that subdomain is treated as external"""
    html = """
    <html>
    <body>
        <a href="https://sub.example.com">Subdomain</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert len(result["external"]) == 1
    assert "https://sub.example.com" in result["external"]


def test_case_insensitive_domain():
    """Test that domain comparison is case-insensitive"""
    html = """
    <html>
    <body>
        <a href="https://Example.COM/page">Internal</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert len(result["internal"]) == 1


def test_mixed_links():
    """Test comprehensive mixed link scenario"""
    html = """
    <html>
    <body>
        <a href="/internal1">Internal 1</a>
        <a href="/internal2">Internal 2</a>
        <a href="https://example.com/internal3">Internal 3</a>
        <a href="https://external1.com">External 1</a>
        <a href="https://external2.com">External 2</a>
        <a href="https://external2.com/page">External 2 Page</a>
        <a href="#anchor1">Anchor 1</a>
        <a href="#anchor2">Anchor 2</a>
        <a href="mailto:test@example.com">Email</a>
    </body>
    </html>
    """
    analyzer = LinkAnalyzer()
    result = analyzer.analyze(html, "https://example.com")
    
    assert len(result["internal"]) == 3
    assert len(result["external"]) == 3
    assert len(result["anchors"]) == 2
    assert result["total"] == 8
    assert result["by_domain"]["external1.com"] == 1
    assert result["by_domain"]["external2.com"] == 2
