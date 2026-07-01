"""
Tests for metadata extraction
"""

import importlib
import json

from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.core.metadata_jsonld import _load_deep_graph_jsonld_text


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


def test_extract_language_from_content_language_list_uses_primary_language():
    """Comma-separated content-language lists should return the primary language."""
    html = """
    <html>
    <head>
        <meta http-equiv="content-language" content="en,fr">
    </head>
    <body>
        Content with enough words to trigger language detection fallback fallback
        fallback fallback fallback fallback fallback fallback fallback fallback
        fallback fallback fallback.
    </body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["language"] == "en"


def test_content_language_detection_is_deterministic():
    """langdetect must be seeded so detection is reproducible across runs."""
    detector_factory = getattr(importlib.import_module("langdetect"), "DetectorFactory")

    detector_factory.seed = 12345  # simulate a fresh process with a random seed
    html = """
    <html>
    <head><title>Test</title></head>
    <body>
        This is a paragraph of plain English content that is long enough to
        trigger the langdetect fallback because no language is declared on the
        document anywhere at all in the markup here today.
    </body>
    </html>
    """
    extractor = MetadataExtractor()
    extractor.extract(html, "https://example.com")
    assert detector_factory.seed == 0


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


def test_extract_canonical_url_accepts_tokenized_rel_values():
    html = """
    <html>
    <head>
        <link rel="canonical alternate" href="https://example.com/canonical">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com/page")
    assert metadata["canonical_url"] == "https://example.com/canonical"


def test_extract_canonical_url_uses_base_href_for_relative_urls():
    html = """
    <html>
    <head>
        <base href="https://cdn.example.com/sub/">
        <link rel="canonical" href="article">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com/page")
    assert metadata["canonical_url"] == "https://cdn.example.com/sub/article"


def test_extract_og_url_uses_base_href_for_relative_urls():
    html = """
    <html>
    <head>
        <base href="https://cdn.example.com/sub/">
        <meta property="og:url" content="article">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com/page")
    assert metadata["canonical_url"] == "https://cdn.example.com/sub/article"


def test_extract_canonical_url_rejects_non_http_scheme():
    """Canonical URLs must stay on http/https; invalid schemes fall back to the page URL."""
    html = """
    <html>
    <head>
        <link rel="canonical" href="javascript:alert(1)">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com/page")
    assert metadata["canonical_url"] == "https://example.com/page"


def test_extract_canonical_url_skips_invalid_candidates_until_valid_one():
    html = """
    <html>
    <head>
        <link rel="canonical" href="javascript:alert(1)">
        <link rel="canonical alternate" href="https://example.com/good">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com/page")
    assert metadata["canonical_url"] == "https://example.com/good"


def test_extract_canonical_url_skips_empty_candidate_until_valid_one():
    html = """
    <html>
    <head>
        <link rel="canonical" href="">
        <link rel="canonical alternate" href="https://example.com/good">
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com/page")
    assert metadata["canonical_url"] == "https://example.com/good"


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


def test_detect_content_type_ignores_partial_comment_and_api_doc_classes():
    """Partial class names should not promote arbitrary pages to forum/documentation."""
    extractor = MetadataExtractor()

    forum_like_html = """
    <html>
    <body>
        <div class="not-comment">Release notes</div>
        <p>Plain documentation content.</p>
    </body>
    </html>
    """
    doc_like_html = """
    <html>
    <body>
        <div class="not-api-doc">Release notes</div>
        <p>Plain documentation content.</p>
    </body>
    </html>
    """

    assert extractor.extract(forum_like_html, "https://example.com")["content_type"] == "webpage"
    assert extractor.extract(doc_like_html, "https://example.com")["content_type"] == "webpage"


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


def test_extract_jsonld_author_and_date_from_root_array():
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        [
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "author": {"name": "Array Author"},
                "datePublished": "2024-01-15"
            }
        ]
        </script>
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["author"] == "Array Author"
    assert metadata["published_date"] == "2024-01-15"


def test_extract_jsonld_author_and_modified_date_from_graph():
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Article",
                    "author": {"name": "Graph Author"},
                    "dateModified": "2024-01-20"
                }
            ]
        }
        </script>
    </head>
    <body>Content</body>
    </html>
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")
    assert metadata["author"] == "Graph Author"
    assert metadata["modified_date"] == "2024-01-20"


def test_extract_jsonld_deep_graph_does_not_crash_metadata_extraction():
    jsonld = json.dumps({"author": {"name": "Deep Author"}})
    for _ in range(1500):
        jsonld = f'{{"@graph":{jsonld}}}'
    html = f"""
    <html>
    <head>
        <script type="application/ld+json">{jsonld}</script>
    </head>
    <body>Content</body>
    </html>
    """

    extractor = MetadataExtractor()
    metadata = extractor.extract(html, "https://example.com")

    assert metadata["author"] == "Deep Author"


def test_deep_jsonld_graph_loader_peels_graph_wrappers_iteratively():
    jsonld = json.dumps({"author": {"name": "Fallback Author"}})
    for _ in range(1500):
        jsonld = f'{{ "@graph" : {jsonld} }}'

    data = _load_deep_graph_jsonld_text(jsonld)

    assert data == {"author": {"name": "Fallback Author"}}


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
