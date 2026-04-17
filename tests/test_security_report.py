"""
Tests for SecurityReport functionality
"""

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from markdown_ingress import generate_security_report
from markdown_ingress.core.config import Config
from markdown_ingress.models import SecurityReport
from markdown_ingress.reporting import report_filename


@pytest.fixture(scope="module")
def local_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = b"<html><body><article><h1>Example Domain</h1><p>Local report content.</p></article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class TestSecurityReport:
    """Test comprehensive security reporting"""

    def test_generate_security_report_basic(self, local_server):
        """Generate security report from local URL"""
        report = generate_security_report(local_server, mode="fast")

        assert isinstance(report, SecurityReport)
        assert report.url == local_server
        assert report.risk_level.lower() in ["safe", "low", "medium", "high", "critical"]
        assert 0.0 <= report.injection_score <= 1.0
        assert report.version == "0.8.0"
        assert report.token_estimate > 0
        assert report.content_hash.startswith("sha256:")
        assert report.structural_hash.startswith("sha256:")

    def test_security_report_to_dict(self):
        """Convert SecurityReport to dictionary"""
        report = SecurityReport(
            injection_score=0.25,
            risk_level="LOW",
            url="http://test.com",
            title="Test Page",
            token_estimate=500,
        )

        data = report.to_dict()

        assert isinstance(data, dict)
        assert data["injection_score"] == 0.25
        assert data["risk_level"] == "LOW"
        assert data["url"] == "http://test.com"
        assert data["version"] == "0.8.0"

    def test_security_report_to_json(self):
        """Export SecurityReport to JSON string"""
        report = SecurityReport(
            injection_score=0.5,
            risk_level="MEDIUM",
            url="http://test.com",
            flags=["hidden_content", "suspicious_pattern"],
        )

        json_str = report.to_json()

        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert data["injection_score"] == 0.5
        assert data["risk_level"] == "MEDIUM"
        assert "hidden_content" in data["flags"]

    def test_security_report_from_dict(self):
        """Create SecurityReport from dictionary"""
        data = {
            "injection_score": 0.75,
            "risk_level": "HIGH",
            "url": "http://test.com",
            "title": "Test",
            "token_estimate": 1000,
            "pattern_matches": [],
            "flags": ["test_flag"],
            "hidden_content_detected": False,
            "hidden_elements_count": 0,
            "imperative_density": 0.0,
            "timestamp": "2024-01-01T00:00:00",
            "version": "0.8.0",
            "token_reduction_percent": 50.0,
            "original_size_bytes": 2000,
            "cleaned_size_bytes": 1000,
            "content_hash": "sha256:abc",
            "structural_hash": "sha256:def",
            "removed_elements": {},
        }

        report = SecurityReport.from_dict(data)

        assert report.injection_score == 0.75
        assert report.risk_level == "HIGH"
        assert report.url == "http://test.com"
        assert report.flags == ["test_flag"]

    def test_security_report_from_json(self):
        """Create SecurityReport from JSON string"""
        json_str = """{
            "injection_score": 0.3,
            "risk_level": "LOW",
            "pattern_matches": [],
            "flags": [],
            "hidden_content_detected": false,
            "hidden_elements_count": 0,
            "imperative_density": 0.0,
            "url": "http://example.com",
            "title": "Example",
            "timestamp": "2024-01-01T00:00:00",
            "version": "0.8.0",
            "token_estimate": 200,
            "token_reduction_percent": 80.0,
            "original_size_bytes": 1000,
            "cleaned_size_bytes": 200,
            "content_hash": "sha256:test",
            "structural_hash": "sha256:test2",
            "removed_elements": {}
        }"""

        report = SecurityReport.from_json(json_str)

        assert report.injection_score == 0.3
        assert report.risk_level == "LOW"
        assert report.url == "http://example.com"
        assert report.token_estimate == 200

    def test_security_report_save_and_load(self):
        """Save and load SecurityReport to/from file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "report.json"

            # Create and save report
            original_report = SecurityReport(
                injection_score=0.45,
                risk_level="MEDIUM",
                url="http://test.com",
                title="Test Document",
                token_estimate=750,
                flags=["test_flag_1", "test_flag_2"],
            )
            original_report.save(str(filepath))

            # Load report
            loaded_report = SecurityReport.load(str(filepath))

            assert loaded_report.injection_score == original_report.injection_score
            assert loaded_report.risk_level == original_report.risk_level
            assert loaded_report.url == original_report.url
            assert loaded_report.flags == original_report.flags

    def test_security_report_json_structure(self, local_server):
        """Verify JSON structure contains all expected fields"""
        report = generate_security_report(local_server, mode="fast")

        json_str = report.to_json()
        data = json.loads(json_str)

        # Check all required fields exist
        required_fields = [
            "injection_score",
            "risk_level",
            "pattern_matches",
            "flags",
            "hidden_content_detected",
            "hidden_elements_count",
            "imperative_density",
            "url",
            "title",
            "timestamp",
            "version",
            "token_estimate",
            "token_reduction_percent",
            "original_size_bytes",
            "cleaned_size_bytes",
            "content_hash",
            "structural_hash",
            "removed_elements",
        ]

        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_generate_security_report_auto_saves_when_enabled(self, local_server, tmp_path):
        report = generate_security_report(
            local_server,
            config=Config(save_reports=True, reports_dir=str(tmp_path)),
            mode="fast",
        )

        saved_reports = list(tmp_path.glob("*.json"))
        assert len(saved_reports) == 1
        assert saved_reports[0].name.endswith(".json")
        assert "127-0-0-1" in saved_reports[0].name

        loaded = SecurityReport.load(str(saved_reports[0]))
        assert loaded.url == report.url
        assert loaded.content_hash == report.content_hash

    def test_report_filename_normalizes_timestamp_to_utc(self):
        report = SecurityReport(
            injection_score=0.2,
            risk_level="LOW",
            url="https://example.com",
        )
        report.timestamp = "2026-04-09T18:34:12+02:00"

        assert report_filename(report) == "20260409T163412Z-example-com.json"

    def test_generate_security_report_save_failure_raises(self, local_server, tmp_path):
        with patch.object(SecurityReport, "save", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                generate_security_report(
                    local_server,
                    config=Config(save_reports=True, reports_dir=str(tmp_path / "reports")),
                    mode="fast",
                )

    def test_security_report_with_pattern_matches(self):
        """SecurityReport can store pattern match details"""
        report = SecurityReport(
            injection_score=0.8,
            risk_level="HIGH",
            pattern_matches=[
                {"pattern": "ignore previous", "location": "line 5"},
                {"pattern": "system prompt", "location": "line 12"},
            ],
        )

        assert len(report.pattern_matches) == 2
        assert report.pattern_matches[0]["pattern"] == "ignore previous"

        # Verify JSON serialization works
        json_str = report.to_json()
        data = json.loads(json_str)
        assert len(data["pattern_matches"]) == 2

    def test_security_report_roundtrip(self):
        """SecurityReport survives JSON roundtrip without data loss"""
        original = SecurityReport(
            injection_score=0.67,
            risk_level="MEDIUM",
            url="http://roundtrip-test.com",
            title="Roundtrip Test",
            flags=["flag1", "flag2", "flag3"],
            hidden_content_detected=True,
            hidden_elements_count=5,
            token_estimate=1234,
            content_hash="sha256:original",
            structural_hash="sha256:structural",
        )

        # To JSON and back
        json_str = original.to_json()
        restored = SecurityReport.from_json(json_str)

        assert restored.injection_score == original.injection_score
        assert restored.risk_level == original.risk_level
        assert restored.url == original.url
        assert restored.title == original.title
        assert restored.flags == original.flags
        assert restored.hidden_content_detected == original.hidden_content_detected
        assert restored.hidden_elements_count == original.hidden_elements_count
        assert restored.token_estimate == original.token_estimate
        assert restored.content_hash == original.content_hash
        assert restored.structural_hash == original.structural_hash
