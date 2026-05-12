"""
Benchmarking utilities for MarkDownIngress
"""

import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from markdown_ingress import ingest
from markdown_ingress.core.interfaces import IFetcher

_logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Results from a benchmark run"""

    url: str
    mode: str

    # Timing
    avg_time_ms: float
    min_time_ms: float
    max_time_ms: float
    std_dev_ms: float

    # Token metrics
    original_tokens: int
    cleaned_tokens: int
    tokens_saved: int
    reduction_percent: float

    # Size metrics
    original_size_bytes: int
    cleaned_size_bytes: int
    size_reduction_percent: float

    # Quality
    injection_score: float
    risk_level: str

    # Run details
    iterations: int = 1
    errors: int = 0
    extractor_comparison: dict[str, dict] | None = None


class Benchmark:
    """Benchmark MarkDownIngress performance"""

    def __init__(
        self,
        model: str = "gpt-4",
        fetcher_factory: Callable[[], IFetcher] | None = None,
        compare_fn: Callable[[str, str], dict] | None = None,
    ):
        self.model = model
        self._fetcher_factory = fetcher_factory
        self._compare_fn = compare_fn

    def run_single(
        self,
        url: str,
        mode: Literal["fast", "render", "auto"] = "fast",
        iterations: int = 3,
        compare_extractors_enabled: bool = False,
    ) -> BenchmarkResult:
        """
        Benchmark a single URL.

        Args:
            url: URL to benchmark
            mode: Processing mode ('fast' or 'render')
            iterations: Number of runs to average

        Returns:
            BenchmarkResult with metrics
        """
        timings = []
        last_doc = None
        errors = 0

        # Run multiple iterations
        for _ in range(iterations):
            try:
                start = time.perf_counter()
                doc = ingest(url, mode=mode, model=self.model)
                end = time.perf_counter()

                timings.append((end - start) * 1000)  # Convert to ms
                last_doc = doc

            except Exception as e:
                errors += 1
                _logger.debug("Benchmark iteration failed: %s", e)

        if not timings or not last_doc:
            raise ValueError(f"All benchmark iterations failed for {url}")

        # Calculate timing stats
        avg_time = statistics.mean(timings)
        min_time = min(timings)
        max_time = max(timings)
        std_dev = statistics.stdev(timings) if len(timings) > 1 else 0.0

        # Get token metrics from last successful run
        token_savings = last_doc.metadata.get("token_savings", {})
        original_tokens = token_savings.get("html_tokens", 0)
        cleaned_tokens = last_doc.token_estimate

        # Ensure tokens_saved is always non-negative, whether from metadata or calculated
        tokens_saved = max(0, token_savings.get("saved_tokens", original_tokens - cleaned_tokens))
        # Ensure reduction_percent is always non-negative, cap at 100%
        raw_percent = token_savings.get(
            "savings_percent",
            (tokens_saved / original_tokens * 100) if original_tokens > 0 else 0.0,
        )
        reduction_percent = max(0.0, min(100.0, raw_percent))

        # Size metrics
        original_size = last_doc.metadata.get("original_size_bytes", 0)
        cleaned_size = last_doc.metadata.get(
            "cleaned_size_bytes", len(last_doc.markdown.encode("utf-8"))
        )
        size_reduction = max(
            0.0,
            (original_size - cleaned_size) / original_size * 100 if original_size > 0 else 0.0,
        )
        extractor_comparison = None
        if compare_extractors_enabled:
            if self._compare_fn is None:
                _logger.warning("compare_extractors not available (no compare_fn provided)")
            else:
                try:
                    if self._fetcher_factory is None:
                        raise RuntimeError("No fetcher_factory provided to Benchmark.")
                    fetcher = self._fetcher_factory()
                    try:
                        fetch_result = fetcher.fetch_sync(url)
                    finally:
                        close = getattr(fetcher, "close", None)
                        if callable(close):
                            close()
                    extractor_comparison = self._compare_fn(fetch_result.html, self.model)
                except Exception as e:
                    _logger.debug("Extractor comparison failed: %s", e)

        return BenchmarkResult(
            url=url,
            mode=mode,
            avg_time_ms=avg_time,
            min_time_ms=min_time,
            max_time_ms=max_time,
            std_dev_ms=std_dev,
            original_tokens=original_tokens,
            cleaned_tokens=cleaned_tokens,
            tokens_saved=tokens_saved,
            reduction_percent=reduction_percent,
            original_size_bytes=original_size,
            cleaned_size_bytes=cleaned_size,
            size_reduction_percent=size_reduction,
            injection_score=last_doc.injection_score,
            risk_level=last_doc.metadata.get("risk_level", "unknown"),
            iterations=iterations,
            errors=errors,
            extractor_comparison=extractor_comparison,
        )

    def run_batch(
        self,
        urls: list[str],
        mode: Literal["fast", "render", "auto"] = "fast",
        iterations: int = 3,
        compare_extractors_enabled: bool = False,
    ) -> list[BenchmarkResult]:
        """
        Benchmark multiple URLs.

        Args:
            urls: List of URLs to benchmark
            mode: Processing mode
            iterations: Iterations per URL

        Returns:
            List of BenchmarkResult objects
        """
        results = []

        for url in urls:
            try:
                result = self.run_single(
                    url,
                    mode=mode,
                    iterations=iterations,
                    compare_extractors_enabled=compare_extractors_enabled,
                )
                results.append(result)
            except Exception as e:
                # Skip failed URLs but log for debugging
                _logger.debug("Benchmark failed for URL %s: %s", url, e)

        return results

    def compare_modes(self, url: str, iterations: int = 3) -> dict[str, BenchmarkResult]:
        """
        Compare fast vs render mode for a URL.

        Args:
            url: URL to test
            iterations: Iterations per mode

        Returns:
            Dictionary with 'fast' and 'render' results
        """
        results = {}

        # Benchmark fast mode
        try:
            results["fast"] = self.run_single(url, mode="fast", iterations=iterations)
        except Exception as e:
            _logger.debug("Fast mode benchmark failed: %s", e)

        # Benchmark render mode (if available)
        try:
            results["render"] = self.run_single(url, mode="render", iterations=iterations)
        except Exception as e:
            _logger.debug("Render mode benchmark failed: %s", e)

        return results

    def generate_report(self, results: list[BenchmarkResult]) -> str:
        """
        Generate a text report from benchmark results.

        Args:
            results: List of benchmark results

        Returns:
            Formatted report string
        """
        if not results:
            return "No benchmark results"

        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("MarkDownIngress Benchmark Report")
        report_lines.append("=" * 80)
        report_lines.append("")

        for i, result in enumerate(results, 1):
            report_lines.append(f"{i}. {result.url}")
            report_lines.append(f"   Mode: {result.mode}")
            report_lines.append(
                f"   Timing: {result.avg_time_ms:.1f}ms avg "
                f"(min: {result.min_time_ms:.1f}ms, max: {result.max_time_ms:.1f}ms)"
            )
            token_pct = (
                f"-{result.reduction_percent:.1f}%" if result.reduction_percent > 0 else "0.0%"
            )
            size_pct = (
                f"-{result.size_reduction_percent:.1f}%"
                if result.size_reduction_percent > 0
                else "0.0%"
            )
            report_lines.append(
                f"   Tokens: {result.original_tokens:,} → {result.cleaned_tokens:,} ({token_pct})"
            )
            report_lines.append(
                f"   Size: {result.original_size_bytes:,} → "
                f"{result.cleaned_size_bytes:,} bytes ({size_pct})"
            )
            report_lines.append(f"   Security: {result.injection_score:.3f} ({result.risk_level})")
            if result.extractor_comparison:
                extractors = ", ".join(
                    f"{name}:{data['markdown_length']}"
                    for name, data in result.extractor_comparison.items()
                    if data.get("available")
                )
                report_lines.append(f"   Extractors: {extractors}")
            report_lines.append("")

        # Summary
        avg_time = statistics.mean([r.avg_time_ms for r in results])
        avg_reduction = statistics.mean([r.reduction_percent for r in results])
        extractor_summary: dict[str, list[float]] = {}
        for result in results:
            if not result.extractor_comparison:
                continue
            for name, data in result.extractor_comparison.items():
                if data.get("available"):
                    extractor_summary.setdefault(name, []).append(
                        float(data.get("token_estimate", 0))
                    )

        report_lines.append("=" * 80)
        report_lines.append("Summary")
        report_lines.append("=" * 80)
        report_lines.append(f"URLs tested: {len(results)}")
        report_lines.append(f"Average time: {avg_time:.1f}ms")
        report_lines.append(f"Average token reduction: {avg_reduction:.1f}%")
        if extractor_summary:
            report_lines.append("Extractor token averages:")
            for name, values in sorted(extractor_summary.items()):
                report_lines.append(f"- {name}: {statistics.mean(values):.1f} tokens")
        report_lines.append("")

        return "\n".join(report_lines)
