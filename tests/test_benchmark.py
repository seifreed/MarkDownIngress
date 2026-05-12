"""
Tests for benchmarking utilities
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markdown_ingress.core.benchmark import Benchmark, BenchmarkResult


@pytest.fixture(scope="module")
def local_servers():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = (
                b"<html><body><article><h1>Benchmark Test</h1>"
                b"<p>Local content.</p></article></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    servers = []
    urls = []
    for _ in range(2):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        urls.append(f"http://127.0.0.1:{server.server_address[1]}")

    yield urls

    for server in servers:
        server.shutdown()


class TestBenchmark:
    """Test benchmarking functionality"""

    def test_run_single_benchmark(self, local_servers):
        """Benchmark a single URL"""
        bench = Benchmark(model="gpt-4")

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
        bench = Benchmark()

        result = bench.run_single(local_servers[0], iterations=1)

        assert result.cleaned_tokens > 0
        assert result.cleaned_size_bytes > 0
        assert 0.0 <= result.injection_score <= 1.0
        assert result.risk_level in ["safe", "low", "medium", "high", "critical"]

    def test_run_batch_benchmark(self, local_servers):
        """Benchmark multiple URLs"""
        bench = Benchmark()

        urls = local_servers

        results = bench.run_batch(urls, iterations=1)

        assert len(results) >= 1  # At least one should succeed
        assert all(isinstance(r, BenchmarkResult) for r in results)

    def test_generate_report(self, local_servers):
        """Generate text report from results"""
        bench = Benchmark()

        results = [bench.run_single(local_servers[0], iterations=1)]

        report = bench.generate_report(results)

        assert "Benchmark Report" in report
        assert "127.0.0.1" in report
        assert "Timing:" in report
        assert "Tokens:" in report
        assert "Summary" in report
