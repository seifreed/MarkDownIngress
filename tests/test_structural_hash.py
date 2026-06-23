"""
Tests for structural hashing functionality
"""

from markdown_ingress.core.hashing import Hasher


class TestStructuralHash:
    """Test structural hash generation"""

    def test_same_structure_different_content_same_hash(self):
        """Same heading structure should produce same structural hash"""
        hasher = Hasher()

        doc1 = """# Main Title
## Section 1
Some content here about topic A.

## Section 2
More content about topic B.
"""

        doc2 = """# Main Title
## Section 1
Completely different content about something else.

## Section 2
Also different content here.
"""

        hash1 = hasher.hash_structural(doc1)
        hash2 = hasher.hash_structural(doc2)

        assert hash1 == hash2, "Same structure should produce same hash"

    def test_different_structure_different_hash(self):
        """Different heading structure should produce different hash"""
        hasher = Hasher()

        doc1 = """# Main Title
## Section 1
Content here.
"""

        doc2 = """# Main Title
## Section 1
### Subsection
Content here.
"""

        hash1 = hasher.hash_structural(doc1)
        hash2 = hasher.hash_structural(doc2)

        assert hash1 != hash2, "Different structure should produce different hash"

    def test_heading_level_changes_affect_hash(self):
        """Changing heading levels should change structural hash"""
        hasher = Hasher()

        doc1 = """# Title
## Section
### Subsection
"""

        doc2 = """# Title
## Section
## Subsection
"""

        hash1 = hasher.hash_structural(doc1)
        hash2 = hasher.hash_structural(doc2)

        assert hash1 != hash2

    def test_list_structure_detection(self):
        """Lists should be detected in structural hash"""
        hasher = Hasher()

        doc_with_list = """# Title
- Item 1
- Item 2
  - Nested item
"""

        doc_without_list = """# Title
Some paragraph text.
"""

        hash1 = hasher.hash_structural(doc_with_list)
        hash2 = hasher.hash_structural(doc_without_list)

        assert hash1 != hash2

    def test_code_blocks_affect_structure(self):
        """Code blocks should affect structural hash"""
        hasher = Hasher()

        doc_with_code = """# Title
```python
code here
```
"""

        doc_without_code = """# Title
No code here.
"""

        hash1 = hasher.hash_structural(doc_with_code)
        hash2 = hasher.hash_structural(doc_without_code)

        assert hash1 != hash2

    def test_links_count_in_structure(self):
        """Link count should be part of structural fingerprint"""
        hasher = Hasher()

        doc_with_links = """# Title
Check [this link](http://example.com) and [another](http://test.com).
"""

        doc_without_links = """# Title
No links here.
"""

        hash1 = hasher.hash_structural(doc_with_links)
        hash2 = hasher.hash_structural(doc_without_links)

        assert hash1 != hash2

    def test_empty_content_fallback(self):
        """Empty content should still produce valid hash"""
        hasher = Hasher()

        hash_result = hasher.hash_structural("")

        assert hash_result.startswith("sha256:")
        assert len(hash_result) > 10

    def test_plain_text_fallback(self):
        """Plain text without structure should use content fallback"""
        hasher = Hasher()

        doc1 = "Just plain text without any markdown structure."
        doc2 = "Different plain text content here."

        hash1 = hasher.hash_structural(doc1)
        hash2 = hasher.hash_structural(doc2)

        assert hash1 != hash2

    def test_heading_case_insensitive(self):
        """Heading titles should be normalized (case-insensitive)"""
        hasher = Hasher()

        doc1 = """# Hello World
## Section One
"""

        doc2 = """# hello world
## section one
"""

        hash1 = hasher.hash_structural(doc1)
        hash2 = hasher.hash_structural(doc2)

        assert hash1 == hash2

    def test_heading_punctuation_normalized(self):
        """Heading punctuation should be normalized"""
        hasher = Hasher()

        doc1 = """# Hello, World!
## Section: One
"""

        doc2 = """# Hello World
## Section One
"""

        hash1 = hasher.hash_structural(doc1)
        hash2 = hasher.hash_structural(doc2)

        assert hash1 == hash2

    def test_paragraph_content_changes_dont_affect_hash(self):
        """Changing paragraph content shouldn't change structural hash"""
        hasher = Hasher()

        doc1 = """# Article
## Introduction
This is a very long paragraph with lots of details about the topic.
It contains multiple sentences explaining various aspects.

## Conclusion
Final thoughts here.
"""

        doc2 = """# Article
## Introduction
Short paragraph.

## Conclusion
Different conclusion.
"""

        hash1 = hasher.hash_structural(doc1)
        hash2 = hasher.hash_structural(doc2)

        assert hash1 == hash2

    def test_deterministic_hashing(self):
        """Same content should always produce same structural hash"""
        hasher = Hasher()

        doc = """# Test Document
## Section A
### Subsection A1
Content here.

## Section B
- Item 1
- Item 2

[Link](http://example.com)
"""

        hash1 = hasher.hash_structural(doc)
        hash2 = hasher.hash_structural(doc)
        hash3 = hasher.hash_structural(doc)

        assert hash1 == hash2 == hash3

    def test_unclosed_link_paren_is_not_redos(self):
        """Adversarial markdown with an unclosed link paren must hash in
        linear time, not quadratic. Before the possessive-quantifier fix this
        input took minutes; it now completes well under a second."""
        import time

        hasher = Hasher()
        payload = "[x](" + "a" * 200_000

        start = time.perf_counter()
        hasher.hash_structural(payload)
        assert time.perf_counter() - start < 1.0
