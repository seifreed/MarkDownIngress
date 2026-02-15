"""
Scoring module - Calculate final injection risk score
"""

from markdown_ingress.models import InjectionAnalysis


class Scorer:
    """Calculate and interpret injection risk scores"""
    
    # Risk level thresholds
    RISK_LEVELS = {
        'safe': (0.0, 0.2),
        'low': (0.2, 0.4),
        'medium': (0.4, 0.6),
        'high': (0.6, 0.8),
        'critical': (0.8, 1.0),
    }
    
    def __init__(self):
        pass
    
    def get_risk_level(self, score: float) -> str:
        """
        Get risk level name from score.
        
        Args:
            score: Injection score (0.0 - 1.0)
            
        Returns:
            Risk level string
        """
        for level, (min_score, max_score) in self.RISK_LEVELS.items():
            if min_score <= score < max_score:
                return level
        
        # Edge case: exactly 1.0
        if score == 1.0:
            return 'critical'
        
        return 'unknown'
    
    def should_block(self, analysis: InjectionAnalysis, threshold: float = 0.7) -> bool:
        """
        Determine if content should be blocked based on score.
        
        Args:
            analysis: Injection analysis result
            threshold: Score threshold for blocking (default: 0.7)
            
        Returns:
            True if content should be blocked
        """
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
            'safe': 'Content appears safe for LLM ingestion.',
            'low': 'Low risk detected. Review recommended but likely safe.',
            'medium': 'Medium risk detected. Manual review recommended before use.',
            'high': 'High risk detected. Content may contain injection attempts. Use with caution.',
            'critical': 'Critical risk detected. Content likely contains prompt injection. Blocking recommended.',
        }
        
        return recommendations.get(risk_level, 'Unable to assess risk.')
