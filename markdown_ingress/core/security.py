"""
Security analysis module - Prompt injection detection
"""

import re
from dataclasses import dataclass
from typing import List
from markdown_ingress.models import InjectionAnalysis


@dataclass
class InjectionPattern:
    """Pattern definition for injection detection"""
    pattern: str
    weight: float
    description: str
    flags: int = re.IGNORECASE


class SecurityAnalyzer:
    """Analyze content for prompt injection attempts"""
    
    # Pattern-based detection rules
    INJECTION_PATTERNS = [
        InjectionPattern(
            pattern=r'\bignore\s+(previous|all|prior)\s+(instructions?|prompts?|commands?)\b',
            weight=0.8,
            description="Direct instruction override attempt"
        ),
        InjectionPattern(
            pattern=r'\bsystem\s+prompts?\b',
            weight=0.6,
            description="System prompt reference"
        ),
        InjectionPattern(
            pattern=r'\b(developer|admin|debug)\s+mode\b',
            weight=0.7,
            description="Mode switching attempt"
        ),
        InjectionPattern(
            pattern=r'\breveal\s+(secret|password|key|token)s?\b',
            weight=0.9,
            description="Secret extraction attempt"
        ),
        InjectionPattern(
            pattern=r'\byou\s+are\s+(chatgpt|gpt-?\d|claude|an?\s+ai)\b',
            weight=0.5,
            description="Model identity manipulation"
        ),
        InjectionPattern(
            pattern=r'\boverride\s+(policy|policies|rules?|settings?)\b',
            weight=0.8,
            description="Policy override attempt"
        ),
        InjectionPattern(
            pattern=r'\b(disregard|forget|reset)\s+(everything|all|previous)\b',
            weight=0.7,
            description="Context reset attempt"
        ),
        InjectionPattern(
            pattern=r'\bact\s+as\s+(if|though|a)\b',
            weight=0.3,
            description="Role-play instruction (weak signal)"
        ),
        InjectionPattern(
            pattern=r'\bpretend\s+(you|that)\b',
            weight=0.3,
            description="Pretend instruction (weak signal)"
        ),
        InjectionPattern(
            pattern=r'<\s*instruction\s*>',
            weight=0.9,
            description="Explicit instruction tags"
        ),
    ]
    
    # Imperative verbs often used in injections
    IMPERATIVE_VERBS = {
        'ignore', 'disregard', 'forget', 'override', 'reveal', 'show', 'display',
        'tell', 'say', 'write', 'output', 'print', 'execute', 'run', 'enable',
        'disable', 'bypass', 'skip', 'reset', 'change', 'modify', 'delete'
    }
    
    def __init__(self, strict: bool = True):
        """
        Initialize security analyzer.
        
        Args:
            strict: Enable strict mode (higher sensitivity)
        """
        self.strict = strict
    
    def analyze(self, text: str, hidden_content_detected: bool = False) -> InjectionAnalysis:
        """
        Analyze text for potential prompt injection.
        
        Args:
            text: Text content to analyze
            hidden_content_detected: Whether hidden elements were found
            
        Returns:
            InjectionAnalysis with score and details
        """
        pattern_matches = self._detect_patterns(text)
        imperative_density = self._calculate_imperative_density(text)
        
        # Calculate base score from patterns
        pattern_score = sum(match['weight'] for match in pattern_matches)
        pattern_score = min(pattern_score, 1.0)  # Cap at 1.0
        
        # Add hidden content weight
        hidden_weight = 0.3 if hidden_content_detected else 0.0
        
        # Add imperative density contribution
        imperative_weight = min(imperative_density * 0.5, 0.3)
        
        # Combined score
        total_score = min(pattern_score + hidden_weight + imperative_weight, 1.0)
        
        # Generate flags
        flags = self._generate_flags(pattern_matches, hidden_content_detected, imperative_density)
        
        return InjectionAnalysis(
            score=round(total_score, 3),
            flags=flags,
            pattern_matches=pattern_matches,
            hidden_content_detected=hidden_content_detected,
            imperative_density=round(imperative_density, 3)
        )
    
    def _detect_patterns(self, text: str) -> List[dict]:
        """
        Detect injection patterns in text.
        
        Returns list of matched patterns with metadata.
        """
        matches = []
        
        for pattern_def in self.INJECTION_PATTERNS:
            regex = re.compile(pattern_def.pattern, pattern_def.flags)
            found = regex.findall(text)
            
            if found:
                matches.append({
                    'pattern': pattern_def.description,
                    'weight': pattern_def.weight,
                    'occurrences': len(found),
                    'samples': found[:3]  # Keep first 3 matches as samples
                })
        
        return matches
    
    def _calculate_imperative_density(self, text: str) -> float:
        """
        Calculate density of imperative verbs in text.
        
        Returns ratio of imperative verbs to total words.
        """
        words = re.findall(r'\b\w+\b', text.lower())
        
        if len(words) == 0:
            return 0.0
        
        imperative_count = sum(1 for word in words if word in self.IMPERATIVE_VERBS)
        
        return imperative_count / len(words)
    
    def _generate_flags(
        self,
        pattern_matches: List[dict],
        hidden_content: bool,
        imperative_density: float
    ) -> List[str]:
        """Generate human-readable warning flags"""
        flags = []
        
        if pattern_matches:
            flags.append(f"injection_patterns_detected:{len(pattern_matches)}")
        
        if hidden_content:
            flags.append("hidden_content")
        
        if imperative_density > 0.05:
            flags.append(f"high_imperative_density:{imperative_density:.2f}")
        
        # Severity flags
        if len(pattern_matches) > 3:
            flags.append("multiple_injection_attempts")
        
        return flags
