"""
Scoring module - Calculate final injection risk score
"""

import logging
import math

from markdown_ingress.models import InjectionAnalysis

_logger = logging.getLogger(__name__)


def _coerce_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _ensure_threshold(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"threshold must be a finite number, got {type(value).__name__}")
    threshold = float(value)
    if math.isnan(threshold) or math.isinf(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be between 0.0 and 1.0, got {value}")
    return threshold


class Scorer:
    """Calculate and interpret injection risk scores"""

    DEFAULT_BLOCK_THRESHOLD: float = 0.7

    def __init__(self):
        pass

    def get_risk_level(self, score: float) -> str:
        """
        Get risk level name from score.

        Args:
            score: Injection score (expected 0.0 - 1.0, but values outside are handled)

        Returns:
            Risk level string

        Risk level boundaries (inclusive lower, exclusive upper):
            - safe: [0.0, 0.2)
            - low: [0.2, 0.4)
            - medium: [0.4, 0.6)
            - high: [0.6, 0.8)
            - critical: [0.8, 1.0]
        """
        # Validate and clamp score to valid range
        coerced_score = _coerce_score(score)
        if coerced_score is None:
            _logger.warning(
                "Injection score %r is not numeric, treating as critical (fail-safe)", score
            )
            return "critical"
        original_score = coerced_score
        score = coerced_score
        if math.isnan(score):
            _logger.warning("Injection score is NaN, treating as critical (fail-safe)")
            return "critical"
        if score < 0.0:
            score = 0.0
        elif score > 1.0:
            score = 1.0
        if score != original_score:
            _logger.warning(
                "Clamped out-of-range injection score from %s to %s", original_score, score
            )

        # Handle exact boundary cases explicitly for clarity
        if score >= 0.8:
            return "critical"
        if score >= 0.6:
            return "high"
        if score >= 0.4:
            return "medium"
        if score >= 0.2:
            return "low"
        return "safe"

    def should_block(
        self, analysis: InjectionAnalysis, threshold: float = DEFAULT_BLOCK_THRESHOLD
    ) -> bool:
        """
        Determine if content should be blocked based on score.

        Args:
            analysis: Injection analysis result
            threshold: Score threshold for blocking (default: 0.7, must be 0.0-1.0)

        Returns:
            True if content should be blocked

        Raises:
            ValueError: If threshold is not between 0.0 and 1.0
        """
        threshold = _ensure_threshold(threshold)
        score = _coerce_score(analysis.score)
        # Invalid scores are treated as blocking (fail-safe)
        if score is None or math.isnan(score) or not 0.0 <= score <= 1.0:
            _logger.warning(
                "Injection score %s is invalid, treating as blocking (fail-safe)", analysis.score
            )
            return True
        return score >= threshold

    def get_recommendation(self, analysis: InjectionAnalysis) -> str:
        """
        Get human-readable recommendation based on analysis.

        Args:
            analysis: Injection analysis result

        Returns:
            Recommendation string
        """
        risk_level = self.get_risk_level(analysis.score)

        recommendations = {
            "safe": "Content appears safe for LLM ingestion.",
            "low": "Low risk detected. Review recommended but likely safe.",
            "medium": "Medium risk detected. Manual review recommended before use.",
            "high": "High risk detected. Content may contain injection attempts. Use with caution.",
            "critical": (
                "Critical risk detected. Content likely contains prompt injection. "
                "Blocking recommended."
            ),
        }

        return recommendations[risk_level]
