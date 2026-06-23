"""
Tests for CLI batch command
"""

import json
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

import pytest

from tests.local_http_server import serve_html


def _cli_cmd(*args: str) -> list[str]:
    """Run CLI through current Python interpreter for environment portability."""
    return [sys.executable, "-m", "markdown_ingress.cli", *args]


@pytest.fixture(scope="module")
def local_servers():
    html = b"<html><body><h1>CLI Test</h1><p>Local content.</p></body></html>"
    with ExitStack() as stack:
        yield [stack.enter_context(serve_html(html)) for _ in range(3)]


class TestCLIBatch:
    """Test CLI batch processing"""

    def test_batch_command_basic(self, local_servers):
        """Batch command processes multiple URLs"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create URLs file
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text(f"{local_servers[0]}\n{local_servers[1]}\n")

            output_dir = Path(tmpdir) / "output"

            # Run batch command
            result = subprocess.run(
                _cli_cmd("batch", str(urls_file), "--output", str(output_dir)),
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0
            assert output_dir.exists()

            # Check output files
            md_files = list(output_dir.glob("*.md"))
            assert len(md_files) == 2

    def test_batch_command_json_output(self, local_servers):
        """Batch command can output JSON summary"""
        with tempfile.TemporaryDirectory() as tmpdir:
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text(f"{local_servers[0]}\n")

            output_file = Path(tmpdir) / "results.json"

            result = subprocess.run(
                _cli_cmd("batch", str(urls_file), "--json", "--output", str(output_file)),
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0
            assert output_file.exists()

            # Verify JSON structure
            data = json.loads(output_file.read_text())
            assert "summary" in data
            assert "results" in data
            assert data["summary"]["total"] == 1

    def test_batch_with_comments_and_empty_lines(self, local_servers):
        """Batch command ignores comments and empty lines"""
        with tempfile.TemporaryDirectory() as tmpdir:
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text(f"""
# This is a comment
{local_servers[0]}

# Another comment
{local_servers[1]}

""")

            output_dir = Path(tmpdir) / "output"

            result = subprocess.run(
                _cli_cmd("batch", str(urls_file), "--output", str(output_dir), "--concurrent", "2"),
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0
            md_files = list(output_dir.glob("*.md"))
            assert len(md_files) == 2

    def test_batch_concurrent_limit(self, local_servers):
        """Batch command respects concurrent limit"""
        with tempfile.TemporaryDirectory() as tmpdir:
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text(f"{local_servers[0]}\n{local_servers[1]}\n{local_servers[2]}\n")

            output_file = Path(tmpdir) / "results.json"

            result = subprocess.run(
                _cli_cmd(
                    "batch",
                    str(urls_file),
                    "--concurrent",
                    "1",
                    "--json",
                    "--output",
                    str(output_file),
                ),
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0
            data = json.loads(output_file.read_text())
            assert data["summary"]["total"] == 3


class TestCLIIngest:
    """Test CLI ingest command"""

    def test_ingest_subcommand(self, local_servers):
        """Ingest subcommand works"""
        result = subprocess.run(
            _cli_cmd("ingest", local_servers[0], "--no-content"),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "MarkDownIngress" in result.stdout
        assert "Tokens:" in result.stdout

    def test_ingest_json_output(self, local_servers):
        """Ingest can output JSON"""
        result = subprocess.run(
            _cli_cmd("ingest", local_servers[0], "--json"),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "markdown" in data
        assert "token_estimate" in data
        assert "injection_score" in data

    def test_ingest_save_file(self, local_servers):
        """Ingest can save to file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.md"

            result = subprocess.run(
                _cli_cmd(
                    "ingest",
                    local_servers[0],
                    "--save",
                    str(output_file),
                    "--no-content",
                ),
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0
            assert output_file.exists()
            content = output_file.read_text()
            assert len(content) > 0

    def test_legacy_url_mode(self, local_servers):
        """Legacy mode works via ingest subcommand"""
        result = subprocess.run(
            _cli_cmd("ingest", local_servers[0], "--no-content"),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Tokens:" in result.stdout
