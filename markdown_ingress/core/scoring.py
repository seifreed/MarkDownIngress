"""
Scoring module - Calculate final injection risk score
"""

import logging

from markdown_ingress.models import InjectionAnalysis

_logger = logging.getLogger(__name__)


class Scorer:
    """Calculate and interpret injection risk scores"""

    # Risk level thresholds (upper bound is exclusive except for "critical")
    RISK_LEVELS = {
        "safe": (0.0, 0.2),
        "low": (0.2, 0.4),
        "medium": (0.4, 0.6),
        "high": (0.6, 0.8),
        "critical": (0.8, float("inf")),
    }

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
        original_score = score
        if score < 0.0:
            score = 0.0
        elif score > 1.0:
            score = 1.0
        if score != original_score:
            _logger.warning("Clamped out-of-range injection score from %s to %s", original_score, score)

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

    def should_block(self, analysis: InjectionAnalysis, threshold: float = 0.7) -> bool:
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
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be between 0.0 and 1.0, got {threshold}")
        return analysis.score >= threshold

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
            "critical": "Critical risk detected. Content likely contains prompt injection. Blocking recommended.",
        }

        return recommendations.get(risk_level, "Unable to assess risk.")
