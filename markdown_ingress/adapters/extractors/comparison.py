"""Concrete extractor comparison adapter implementations."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from readability import Document  # type: ignore[import-untyped]

from markdown_ingress.core.security import SecurityAnalyzer
from markdown_ingress.core.tokens import TokenEstimator


@dataclass
class ExtractorComparisonResult:
    extractor: str
    available: bool
    markdown: str
    title: str | None
    markdown_length: int
    token_estimate: int
    heading_count: int
    link_count: int
    code_block_count: int
    table_count: int
    injection_score: float
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["length"] = data["markdown_length"]
        return data


class ExtractorEvaluator:
    """Compare extractors using quality and safety-oriented heuristics."""

    def __init__(self, model: str = "gpt-4"):
        self.token_estimator = TokenEstimator(model=model)
        self.security = SecurityAnalyzer(strict=True)

    def compare(self, html: str) -> dict[str, dict[str, Any]]:
        results = {
            "readability": self._evaluate_readability(html),
            "trafilatura": self._evaluate_trafilatura(html),
        }
        return {name: result.to_dict() for name, result in results.items()}

    def _evaluate_readability(self, html: str) -> ExtractorComparisonResult:
        from markdown_ingress.core.markdown import MarkdownConverter

        try:
            doc = Document(html)
            summary_html = doc.summary(html_partial=False)
            # readability returns HTML — convert to markdown for consistent metrics
            markdown = MarkdownConverter().convert(summary_html)
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as exc:
            return ExtractorComparisonResult(
                extractor="readability",
                available=False,
                markdown="",
                title=None,
                markdown_length=0,
                token_estimate=0,
                heading_count=0,
                link_count=0,
                code_block_count=0,
                table_count=0,
                injection_score=0.0,
                notes=[f"readability failed: {type(exc).__name__}: {exc}"],
            )

        return self._make_result(
            "readability", markdown=markdown, title=doc.title(), available=True
        )

    def _evaluate_trafilatura(self, html: str) -> ExtractorComparisonResult:
        try:
            import trafilatura  # type: ignore[import-not-found,import-untyped]
        except ImportError:
            return ExtractorComparisonResult(
                extractor="trafilatura",
                available=False,
                markdown="",
                title=None,
                markdown_length=0,
                token_estimate=0,
                heading_count=0,
                link_count=0,
                code_block_count=0,
                table_count=0,
                injection_score=0.0,
                notes=["trafilatura not installed"],
            )
        try:
            output = trafilatura.extract(html, output_format="markdown") or ""
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as exc:
            return ExtractorComparisonResult(
                extractor="trafilatura",
                available=False,
                markdown="",
                title=None,
                markdown_length=0,
                token_estimate=0,
                heading_count=0,
                link_count=0,
                code_block_count=0,
                table_count=0,
                injection_score=0.0,
                notes=[f"trafilatura failed: {type(exc).__name__}: {exc}"],
            )
        return self._make_result("trafilatura", markdown=output, title=None, available=True)

    def _make_result(
        self,
        extractor: str,
        *,
        markdown: str,
        title: str | None,
        available: bool,
    ) -> ExtractorComparisonResult:
        analysis = self.security.analyze(markdown)
        return ExtractorComparisonResult(
            extractor=extractor,
            available=available,
            markdown=markdown[:2000],
            title=title,
            markdown_length=len(markdown),
            token_estimate=self.token_estimator.estimate(markdown) if markdown else 0,
            heading_count=len(re.findall(r"(?m)^#{1,6}[ \t]", markdown)),
            link_count=markdown.count("]("),
            code_block_count=len(re.findall(r"(?m)^```", markdown)),
            table_count=self._count_tables(markdown),
            injection_score=analysis.score,
            notes=self._notes(markdown),
        )

    @staticmethod
    def _count_tables(markdown: str) -> int:
        """Count distinct markdown tables (contiguous blocks of |-prefixed lines)."""
        count = 0
        in_table = False
        for line in markdown.splitlines():
            if line.startswith("|"):
                if not in_table:
                    count += 1
                    in_table = True
            else:
                in_table = False
        return count

    def _notes(self, markdown: str) -> list[str]:
        notes: list[str] = []
        if not markdown.strip():
            notes.append("empty_output")
        if len(markdown) < 200:
            notes.append("very_short_output")
        if "```" in markdown:
            notes.append("contains_code_blocks")
        if "|" in markdown:
            notes.append("contains_tables")
        return notes


def compare_extractors(html: str, model: str = "gpt-4") -> dict[str, dict[str, Any]]:
    """Convenience wrapper returning comparative metrics for available extractors."""
    return ExtractorEvaluator(model=model).compare(html)
