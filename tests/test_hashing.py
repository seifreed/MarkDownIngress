"""Tests for deterministic hashing"""

from markdown_ingress.core.hashing import Hasher


def test_hash_determinism():
    """Test that same content produces same hash"""
    hasher = Hasher()

    content = "This is test content."

    hash1 = hasher.hash_content(content)
    hash2 = hasher.hash_content(content)

    assert hash1 == hash2
    assert hash1.startswith("sha256:")


def test_hash_uniqueness():
    """Test that different content produces different hashes"""
    hasher = Hasher()

    content1 = "This is test content."
    content2 = "This is different content."

    hash1 = hasher.hash_content(content1)
    hash2 = hasher.hash_content(content2)

    assert hash1 != hash2


def test_hash_format():
    """Test hash format"""
    hasher = Hasher()

    hash_result = hasher.hash_content("test")

    assert ":" in hash_result
    algorithm, digest = hash_result.split(":", 1)
    assert algorithm == "sha256"
    assert len(digest) == 64  # SHA256 hex digest length


def test_structural_hash():
    """Test structural hash extraction"""
    hasher = Hasher()

    markdown = """
# Main Title

First paragraph content.

## Section 1

Section content here.

## Section 2

More content.
    """

    struct_hash = hasher.hash_structural(markdown)

    assert struct_hash.startswith("sha256:")
    assert len(struct_hash) > 10


def test_structural_hash_similarity():
    """Test that similar structure produces same structural hash"""
    hasher = Hasher()

    md1 = """
# Title
## Section A
First sentence. More details here.
## Section B
Another sentence. Extra info.
    """

    md2 = """
# Title
## Section A
First sentence. Different details here.
## Section B
Another sentence. Different extra info.
    """

    hash1 = hasher.hash_structural(md1)
    hash2 = hasher.hash_structural(md2)

    # Should be same due to same headings and first sentences
    assert hash1 == hash2
