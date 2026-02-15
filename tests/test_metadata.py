"""
Tests for metadata extraction
"""

import pytest
from markdown_ingress.core.metadata_extractor import MetadataExtractor


def test_extract_author_from_meta():
    """Test extracting author from meta tag"""
    html = """
    <html>
    <head>
        <meta name="author" content="John Doe">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["author"] == "John Doe"


def test_extract_author_from_og():
    """Test extracting author from Open Graph tag"""
    html = """
    <html>
    <head>
        <meta property="article:author" content="Jane Smith">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["author"] == "Jane Smith"


def test_extract_published_date():
    """Test extracting published date"""
    html = """
    <html>
    <head>
        <meta property="article:published_time" content="2024-01-15T10:00:00Z">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["published_date"] == "2024-01-15T10:00:00Z"


def test_extract_modified_date():
    """Test extracting modified date"""
    html = """
    <html>
    <head>
        <meta property="article:modified_time" content="2024-01-20T15:30:00Z">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["modified_date"] == "2024-01-20T15:30:00Z"


def test_extract_language_from_html():
    """Test extracting language from html lang attribute"""
    html = """
    <html lang="en-US">
    <head><title>Test</title></head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["language"] == "en"


def test_extract_description():
    """Test extracting description"""
    html = """
    <html>
    <head>
        <meta name="description" content="This is a test page">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["description"] == "This is a test page"


def test_extract_og_description():
    """Test extracting og:description"""
    html = """
    <html>
    <head>
        <meta property="og:description" content="Open Graph description">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["description"] == "Open Graph description"


def test_extract_keywords():
    """Test extracting keywords"""
    html = """
    <html>
    <head>
        <meta name="keywords" content="python, testing, markdown">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["keywords"] == "python, testing, markdown"


def test_extract_canonical_url():
    """Test extracting canonical URL"""
    html = """
    <html>
    <head>
        <link rel="canonical" href="https://example.com/canonical">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["canonical_url"] == "https://example.com/canonical"


def test_extract_site_name():
    """Test extracting site name"""
    html = """
    <html>
    <head>
        <meta property="og:site_name" content="Example Site">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["site_name"] == "Example Site"


def test_detect_content_type_article():
    """Test detecting article content type"""
    html = """
    <html>
    <head><title>Article</title></head>
    <body>
        <article>
            <h1>Test Article</h1>
            <p>Content</p>
        </article>
    </body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["content_type"] == "article"


def test_detect_content_type_documentation():
    """Test detecting documentation content type"""
    html = """
    <html>
    <head><title>Docs</title></head>
    <body>
        <div class="documentation">
            <h1>API Documentation</h1>
        </div>
    </body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["content_type"] == "documentation"


def test_detect_content_type_ecommerce():
    """Test detecting e-commerce content type"""
    html = """
    <html>
    <head><title>Product</title></head>
    <body>
        <div class="product">
            <h1>Widget</h1>
            <span class="price">$19.99</span>
        </div>
    </body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["content_type"] == "ecommerce"


def test_detect_content_type_default():
    """Test default content type"""
    html = """
    <html>
    <head><title>Page</title></head>
    <body>
        <div>
            <h1>Test Page</h1>
            <p>Some content</p>
        </div>
    </body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["content_type"] == "webpage"


def test_extract_schema_org_author():
    """Test extracting author from schema.org JSON-LD"""
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "author": {
                "@type": "Person",
                "name": "Schema Author"
            }
        }
        </script>
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["author"] == "Schema Author"


def test_missing_metadata_returns_none():
    """Test that missing metadata returns None"""
    html = """
    <html>
    <head><title>Simple Page</title></head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["author"] is None
    assert metadata["published_date"] is None
    assert metadata["modified_date"] is None
    assert metadata["description"] is None
    assert metadata["keywords"] is None
    assert metadata["site_name"] is None
