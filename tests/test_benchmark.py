"""
Tests for benchmarking utilities
"""

import subprocess
import sys
from contextlib import ExitStack

import pytest

import markdown_ingress.api as _api
from markdown_ingress.core.benchmark import Benchmark, BenchmarkResult
from tests.local_http_server import serve_html

_BENCHMARK_HTML = (
    b"<html><body><article><h1>Benchmark Test</h1>" b"<p>Local content.</p></article></body></html>"
)


@pytest.fixture(scope="module")
def local_servers():
    with ExitStack() as stack:
        yield [stack.enter_context(serve_html(_BENCHMARK_HTML)) for _ in range(2)]


class TestBenchmark:
    """Test benchmarking functionality"""

    def test_run_single_benchmark(self, local_servers):
        """Benchmark a single URL"""
        bench = Benchmark(model="gpt-4", ingest_func=_api.ingest)

        result = bench.run_single(local_servers[0], mode="fast", iterations=2)

        assert isinstance(result, BenchmarkResult)
        assert result.url == local_servers[0]
        assert result.mode == "fast"
        assert result.avg_time_ms > 0
        assert result.min_time_ms > 0
        assert result.max_time_ms >= result.min_time_ms
        assert result.cleaned_tokens > 0
        assert result.iterations == 2

    def test_benchmark_metrics(self, local_servers):
        """Benchmark captures token and size metrics"""
        bench = Benchmark(ingest_func=_api.ingest)

        result = bench.run_single(local_servers[0], iterations=1)

        assert result.cleaned_tokens > 0
        assert result.cleaned_size_bytes > 0
        assert 0.0 <= result.injection_score <= 1.0
        assert result.risk_level in ["safe", "low", "medium", "high", "critical"]

    def test_run_batch_benchmark(self, local_servers):
        """Benchmark multiple URLs"""
        bench = Benchmark(ingest_func=_api.ingest)

        urls = local_servers

        results = bench.run_batch(urls, iterations=1)

        assert len(results) >= 1  # At least one should succeed
        assert all(isinstance(r, BenchmarkResult) for r in results)

    def test_generate_report(self, local_servers):
        """Generate text report from results"""
        bench = Benchmark(ingest_func=_api.ingest)

        results = [bench.run_single(local_servers[0], iterations=1)]

        report = bench.generate_report(results)

        assert "Benchmark Report" in report
        assert "127.0.0.1" in report
        assert "Timing:" in report
        assert "Tokens:" in report
        assert "Summary" in report


def test_benchmark_default_ingest_can_be_required_and_reset():
    """Benchmark requires an explicit ingest dependency."""
    from markdown_ingress.core.benchmark import Benchmark

    with pytest.raises(TypeError):
        Benchmark()


def test_cli_benchmark_compare_extractors_reports_comparison(local_servers, tmp_path, monkeypatch):
    """CLI benchmark --compare-extractors includes extractor data."""
    monkeypatch.setenv("MDI_ALLOW_LOCAL_URLS", "true")
    urls = tmp_path / "urls.txt"
    report_path = tmp_path / "report.txt"
    urls.write_text(f"{local_servers[0]}\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "markdown_ingress.cli",
            "benchmark",
            str(urls),
            "--iterations",
            "1",
            "--fast",
            "--compare-extractors",
            "--output",
            str(report_path),
        ],
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    report = report_path.read_text(encoding="utf-8")
    assert "Extractors:" in report
    assert "Extractor token averages:" in report


def test_cli_benchmark_fails_when_all_urls_fail(local_servers, tmp_path, monkeypatch):
    """CLI benchmark exits non-zero when no URL produced timing data."""
    monkeypatch.delenv("MDI_ALLOW_LOCAL_URLS", raising=False)
    urls = tmp_path / "urls.txt"
    urls.write_text(f"{local_servers[0]}\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "markdown_ingress.cli",
            "benchmark",
            str(urls),
            "--iterations",
            "1",
            "--fast",
        ],
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "Skipped" in result.stdout
    assert "No benchmark results" in result.stdout
