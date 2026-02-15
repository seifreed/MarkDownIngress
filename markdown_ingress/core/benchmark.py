"""
Benchmarking utilities for MarkDownIngress
"""

import time
import statistics
from typing import List, Dict, Any, Callable
from dataclasses import dataclass, field
from markdown_ingress import ingest
from markdown_ingress.core.tokens import TokenEstimator


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


class Benchmark:
    """Benchmark MarkDownIngress performance"""
    
    def __init__(self, model: str = "gpt-4"):
        """
        Initialize benchmarker.
        
        Args:
            model: LLM model for token estimation
        """
        self.model = model
        self.token_estimator = TokenEstimator(model=model)
    
    def run_single(
        self,
        url: str,
        mode: str = "fast",
        iterations: int = 3
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
                
            except Exception:
                errors += 1
        
        if not timings or not last_doc:
            raise ValueError(f"All benchmark iterations failed for {url}")
        
        # Calculate timing stats
        avg_time = statistics.mean(timings)
        min_time = min(timings)
        max_time = max(timings)
        std_dev = statistics.stdev(timings) if len(timings) > 1 else 0.0
        
        # Get token metrics from last successful run
        original_html = last_doc.metadata.get('original_html', '')
        original_tokens = self.token_estimator.estimate(original_html) if original_html else 0
        cleaned_tokens = last_doc.token_estimate
        
        tokens_saved = original_tokens - cleaned_tokens
        reduction_percent = (tokens_saved / original_tokens * 100) if original_tokens > 0 else 0.0
        
        # Size metrics
        original_size = len(original_html.encode('utf-8'))
        cleaned_size = len(last_doc.markdown.encode('utf-8'))
        size_reduction = (original_size - cleaned_size) / original_size * 100 if original_size > 0 else 0.0
        
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
            risk_level=last_doc.metadata.get('risk_level', 'unknown'),
            iterations=iterations,
            errors=errors
        )
    
    def run_batch(
        self,
        urls: List[str],
        mode: str = "fast",
        iterations: int = 3
    ) -> List[BenchmarkResult]:
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
                result = self.run_single(url, mode=mode, iterations=iterations)
                results.append(result)
            except Exception:
                # Skip failed URLs
                pass
        
        return results
    
    def compare_modes(
        self,
        url: str,
        iterations: int = 3
    ) -> Dict[str, BenchmarkResult]:
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
            results['fast'] = self.run_single(url, mode='fast', iterations=iterations)
        except Exception:
            pass
        
        # Benchmark render mode (if available)
        try:
            results['render'] = self.run_single(url, mode='render', iterations=iterations)
        except Exception:
            pass
        
        return results
    
    def generate_report(self, results: List[BenchmarkResult]) -> str:
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
            report_lines.append(f"   Timing: {result.avg_time_ms:.1f}ms avg (min: {result.min_time_ms:.1f}ms, max: {result.max_time_ms:.1f}ms)")
            report_lines.append(f"   Tokens: {result.original_tokens:,} → {result.cleaned_tokens:,} (-{result.reduction_percent:.1f}%)")
            report_lines.append(f"   Size: {result.original_size_bytes:,} → {result.cleaned_size_bytes:,} bytes (-{result.size_reduction_percent:.1f}%)")
            report_lines.append(f"   Security: {result.injection_score:.3f} ({result.risk_level})")
            report_lines.append("")
        
        # Summary
        avg_time = statistics.mean([r.avg_time_ms for r in results])
        avg_reduction = statistics.mean([r.reduction_percent for r in results])
        
        report_lines.append("=" * 80)
        report_lines.append("Summary")
        report_lines.append("=" * 80)
        report_lines.append(f"URLs tested: {len(results)}")
        report_lines.append(f"Average time: {avg_time:.1f}ms")
        report_lines.append(f"Average token reduction: {avg_reduction:.1f}%")
        report_lines.append("")
        
        return "\n".join(report_lines)
