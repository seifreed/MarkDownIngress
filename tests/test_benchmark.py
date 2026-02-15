"""
Tests for benchmarking utilities
"""

from markdown_ingress.core.benchmark import Benchmark, BenchmarkResult


class TestBenchmark:
    """Test benchmarking functionality"""

    def test_run_single_benchmark(self):
        """Benchmark a single URL"""
        bench = Benchmark(model="gpt-4")

        result = bench.run_single("http://example.com", mode="fast", iterations=2)

        assert isinstance(result, BenchmarkResult)
        assert result.url == "http://example.com"
        assert result.mode == "fast"
        assert result.avg_time_ms > 0
        assert result.min_time_ms > 0
        assert result.max_time_ms >= result.min_time_ms
        assert result.cleaned_tokens > 0
        assert result.iterations == 2

    def test_benchmark_metrics(self):
        """Benchmark captures token and size metrics"""
        bench = Benchmark()

        result = bench.run_single("http://example.com", iterations=1)

        assert result.cleaned_tokens > 0
        assert result.cleaned_size_bytes > 0
        assert 0.0 <= result.injection_score <= 1.0
        assert result.risk_level in ["safe", "low", "medium", "high", "critical"]

    def test_run_batch_benchmark(self):
        """Benchmark multiple URLs"""
        bench = Benchmark()

        urls = ["http://example.com", "http://example.org"]

        results = bench.run_batch(urls, iterations=1)

        assert len(results) >= 1  # At least one should succeed
        assert all(isinstance(r, BenchmarkResult) for r in results)

    def test_generate_report(self):
        """Generate text report from results"""
        bench = Benchmark()

        results = [bench.run_single("http://example.com", iterations=1)]

        report = bench.generate_report(results)

        assert "Benchmark Report" in report
        assert "example.com" in report
        assert "Timing:" in report
        assert "Tokens:" in report
        assert "Summary" in report
