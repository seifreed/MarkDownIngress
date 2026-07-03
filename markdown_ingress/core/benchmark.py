"""
Benchmarking utilities for MarkDownIngress
"""

import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from markdown_ingress.config_validation import Mode
from markdown_ingress.core.interfaces import CompareExtractorsFn, IFetcher
from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)

BENCHMARK_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    RuntimeError,
    TimeoutError,
    TypeError,
    ValueError,
)
_SEPARATOR = "=" * 80


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


class BenchmarkIngest(Protocol):
    """Callable protocol for benchmark ingestion adapters."""

    def __call__(
        self, url: str, *, mode: Mode = "fast", model: str = "gpt-4", **kwargs: object
    ) -> SafeDocument: ...


class Benchmark:
    """Benchmark MarkDownIngress performance"""

    def __init__(
        self,
        *,
        model: str = "gpt-4",
        ingest_func: BenchmarkIngest,
        fetcher_factory: Callable[[], IFetcher] | None = None,
        compare_fn: CompareExtractorsFn | None = None,
    ):
        if ingest_func is None or not callable(ingest_func):
            raise TypeError("Benchmark() requires a callable ingest_func")
        self.model = model
        self._ingest_func = ingest_func
        self._fetcher_factory = fetcher_factory
        self._compare_fn = compare_fn
        self.failures: list[tuple[str, str]] = []

    @staticmethod
    def _safe_percent(value: float, total: float) -> float:
        return max(0.0, min(100.0, (value / total * 100.0) if total > 0 else 0.0))

    def _run_iteration(self, url: str, mode: Mode) -> tuple[SafeDocument, float]:
        start = time.perf_counter()
        doc = self._ingest_func(url, mode=mode, model=self.model)
        end = time.perf_counter()
        return doc, (end - start) * 1000  # Convert to ms

    def _create_comparison_fetcher(self) -> IFetcher:
        if self._fetcher_factory is None:
            raise RuntimeError("No fetcher_factory provided to Benchmark.")
        return self._fetcher_factory()

    def _collect_iterations(
        self, url: str, mode: Mode, iterations: int
    ) -> tuple[list[float], SafeDocument, int]:
        timings: list[float] = []
        last_doc: SafeDocument | None = None
        errors = 0
        last_error: str | None = None

        for _ in range(iterations):
            try:
                doc, duration_ms = self._run_iteration(url, mode)
                timings.append(duration_ms)
                last_doc = doc
            except BENCHMARK_ERRORS as exc:
                errors += 1
                last_error = str(exc)
                _logger.debug("Benchmark iteration failed: %s", exc)

        if not timings or last_doc is None:
            detail = f": {last_error}" if last_error else ""
            raise ValueError(f"All benchmark iterations failed for {url}{detail}")

        return timings, last_doc, errors

    def _build_result(
        self,
        url: str,
        mode: Mode,
        iterations: int,
        timings: list[float],
        last_doc: SafeDocument,
        errors: int,
        extractor_comparison: dict[str, dict] | None,
    ) -> BenchmarkResult:
        avg_time = statistics.mean(timings)
        min_time = min(timings)
        max_time = max(timings)
        std_dev = statistics.stdev(timings) if len(timings) > 1 else 0.0

        token_savings = last_doc.metadata.get("token_savings", {})
        original_tokens = token_savings.get("html_tokens", 0)
        cleaned_tokens = last_doc.token_estimate
        tokens_saved = max(0, token_savings.get("saved_tokens", original_tokens - cleaned_tokens))
        fallback_percent = tokens_saved / original_tokens * 100 if original_tokens > 0 else 0.0
        reduction_percent = max(
            0.0, min(100.0, float(token_savings.get("savings_percent", fallback_percent)))
        )

        original_size = last_doc.metadata.get("original_size_bytes", 0)
        cleaned_size = last_doc.metadata.get(
            "cleaned_size_bytes", len(last_doc.markdown.encode("utf-8"))
        )
        size_reduction = self._safe_percent(original_size - cleaned_size, original_size)

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

    @staticmethod
    def _result_change_pct(value: float) -> str:
        return f"-{value:.1f}%" if value > 0 else "0.0%"

    @staticmethod
    def _format_report_header(title: str) -> list[str]:
        return [_SEPARATOR, title, _SEPARATOR, ""]

    @staticmethod
    def _format_extractor_summary(result: BenchmarkResult) -> str | None:
        if not result.extractor_comparison:
            return None

        extractors = ", ".join(
            f"{name}:{data['markdown_length']}"
            for name, data in result.extractor_comparison.items()
            if data.get("available")
        )
        if not extractors:
            return None
        return f"   Extractors: {extractors}"

    def _format_result_lines(self, index: int, result: BenchmarkResult) -> list[str]:
        return [
            f"{index}. {result.url}",
            f"   Mode: {result.mode}",
            (
                f"   Timing: {result.avg_time_ms:.1f}ms avg "
                f"(min: {result.min_time_ms:.1f}ms, max: {result.max_time_ms:.1f}ms, "
                f"std dev: {result.std_dev_ms:.1f}ms)"
            ),
            (
                f"   Tokens: {result.original_tokens:,} → {result.cleaned_tokens:,} "
                f"({self._result_change_pct(result.reduction_percent)})"
            ),
            (
                f"   Size: {result.original_size_bytes:,} → "
                f"{result.cleaned_size_bytes:,} bytes "
                f"({self._result_change_pct(result.size_reduction_percent)})"
            ),
            f"   Security: {result.injection_score:.3f} ({result.risk_level})",
            "",  # Blank line will be removed only if no extractor summary
        ] + ([extractor_line] if (extractor_line := self._format_extractor_summary(result)) else [])

    @staticmethod
    def _collect_extractor_token_averages(
        results: list[BenchmarkResult],
    ) -> dict[str, list[float]]:
        summary: dict[str, list[float]] = {}
        for result in results:
            if not result.extractor_comparison:
                continue
            for name, data in result.extractor_comparison.items():
                if not data.get("available"):
                    continue
                summary.setdefault(name, []).append(float(data.get("token_estimate", 0)))
        return summary

    def _format_summary_lines(self, results: list[BenchmarkResult]) -> list[str]:
        avg_time = statistics.mean([r.avg_time_ms for r in results])
        avg_reduction = statistics.mean([r.reduction_percent for r in results])
        extractor_summary = self._collect_extractor_token_averages(results)

        lines = self._format_report_header("Summary")
        lines.append(f"URLs tested: {len(results)}")
        lines.append(f"Average time: {avg_time:.1f}ms")
        lines.append(f"Average token reduction: {avg_reduction:.1f}%")
        if extractor_summary:
            lines.append("Extractor token averages:")
            for name, values in sorted(extractor_summary.items()):
                lines.append(f"- {name}: {statistics.mean(values):.1f} tokens")
        lines.append("")
        return lines

    def _compare_extractors(self, url: str) -> dict[str, dict] | None:
        if self._compare_fn is None:
            _logger.warning("compare_extractors not available (no compare_fn provided)")
            return None
        try:
            fetcher = self._create_comparison_fetcher()
            try:
                fetch_result = fetcher.fetch_sync(url)
            finally:
                close = getattr(fetcher, "close", None)
                if callable(close):
                    close()
            return self._compare_fn(fetch_result.html, model=self.model)
        except BENCHMARK_ERRORS as exc:
            _logger.debug("Extractor comparison failed: %s", exc)
            return None

    def run_single(
        self,
        url: str,
        mode: Mode = "fast",
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
        timings, last_doc, errors = self._collect_iterations(url, mode, iterations)
        extractor_comparison = self._compare_extractors(url) if compare_extractors_enabled else None

        return self._build_result(
            url,
            mode,
            iterations,
            timings,
            last_doc,
            errors,
            extractor_comparison,
        )

    def run_batch(
        self,
        urls: list[str],
        mode: Mode = "fast",
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
        self.failures = []

        for url in urls:
            try:
                result = self.run_single(
                    url,
                    mode=mode,
                    iterations=iterations,
                    compare_extractors_enabled=compare_extractors_enabled,
                )
                results.append(result)
            except BENCHMARK_ERRORS as e:
                # Skip failed URLs but record the reason so callers can surface it
                _logger.debug("Benchmark failed for URL %s: %s", url, e)
                self.failures.append((url, str(e)))

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
        except BENCHMARK_ERRORS as e:
            _logger.debug("Fast mode benchmark failed: %s", e)

        # Benchmark render mode (if available)
        try:
            results["render"] = self.run_single(url, mode="render", iterations=iterations)
        except BENCHMARK_ERRORS as e:
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
        report_lines.extend(self._format_report_header("MarkDownIngress Benchmark Report"))

        for i, result in enumerate(results, 1):
            report_lines.extend(self._format_result_lines(i, result))
            report_lines.append("")

        report_lines.extend(self._format_summary_lines(results))

        return "\n".join(report_lines)
