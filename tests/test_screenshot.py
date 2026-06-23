"""
Tests for screenshot capture
"""

import os
import tempfile

import pytest

from markdown_ingress import ingest
from tests.local_http_server import serve_html

# Skip all tests if Playwright is not available
pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def local_server():
    with serve_html(
        b"<html><body><h1>Screenshot Test</h1><p>Local content.</p></body></html>"
    ) as url:
        yield url


def test_screenshot_true_creates_temp_file(local_server):
    """Test that screenshot=True creates a temporary file"""
    doc = ingest(
        url=local_server,
        mode="render",
        screenshot=True,
        extract_metadata=False,
        extract_links=False,
    )

    # Check that screenshot_path is set
    assert doc.screenshot_path is not None

    # Check that file exists
    assert os.path.exists(doc.screenshot_path)

    # Check that it's a PNG file
    assert doc.screenshot_path.endswith(".png")

    # Clean up
    if os.path.exists(doc.screenshot_path):
        os.unlink(doc.screenshot_path)


def test_screenshot_custom_path(local_server):
    """Test that screenshot=path saves to specified path"""
    with tempfile.TemporaryDirectory() as tmpdir:
        screenshot_path = os.path.join(tmpdir, "test_screenshot.png")

        doc = ingest(
            url=local_server,
            mode="render",
            screenshot=screenshot_path,
            extract_metadata=False,
            extract_links=False,
        )

        # Check that screenshot_path matches
        assert doc.screenshot_path == screenshot_path

        # Check that file exists
        assert os.path.exists(screenshot_path)

        # Check that file has content
        assert os.path.getsize(screenshot_path) > 0


def test_screenshot_none_no_file(local_server):
    """Test that screenshot=None doesn't create a file"""
    doc = ingest(
        url=local_server,
        mode="render",
        screenshot=None,
        extract_metadata=False,
        extract_links=False,
    )

    # Check that screenshot_path is None
    assert doc.screenshot_path is None


def test_screenshot_not_available_in_fast_mode(local_server):
    """Test that screenshot is not available in fast mode"""
    doc = ingest(
        url=local_server,
        mode="fast",
        screenshot=True,
        extract_metadata=False,
        extract_links=False,
    )
    assert doc.metadata["mode"] == "fast"
    assert doc.screenshot_path is None


def test_screenshot_metadata_included(local_server):
    """Test that screenshot path is included in metadata when captured"""
    with tempfile.TemporaryDirectory() as tmpdir:
        screenshot_path = os.path.join(tmpdir, "test_meta.png")

        doc = ingest(
            url=local_server,
            mode="render",
            screenshot=screenshot_path,
            extract_metadata=False,
            extract_links=False,
        )

        # Screenshot path should be set
        assert doc.screenshot_path == screenshot_path

        # File should exist
        assert os.path.exists(screenshot_path)


def test_screenshot_json_serialization(local_server):
    """Test that screenshot path is properly serialized in JSON output"""
    import json

    with tempfile.TemporaryDirectory() as tmpdir:
        screenshot_path = os.path.join(tmpdir, "test_json.png")

        doc = ingest(
            url=local_server,
            mode="render",
            screenshot=screenshot_path,
            extract_metadata=False,
            extract_links=False,
        )

        # Create JSON output
        output_data = {
            "markdown": doc.markdown,
            "metadata": doc.metadata,
            "screenshot_path": doc.screenshot_path,
        }

        # Should be serializable
        json_str = json.dumps(output_data)
        # Deserialize and compare paths (handles Windows escaping)
        parsed_data = json.loads(json_str)
        assert parsed_data["screenshot_path"] == doc.screenshot_path
