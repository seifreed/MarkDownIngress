"""
Data models for MarkDownIngress
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SafeDocument:
    """
    Main output structure containing sanitized markdown and metadata.
    """
    markdown: str
    metadata: dict
    token_estimate: int
    content_hash: str
    injection_score: float
    flags: list[str] = field(default_factory=list)
    removed_elements: dict = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate injection score range"""
        if not 0.0 <= self.injection_score <= 1.0:
            raise ValueError(f"injection_score must be between 0.0 and 1.0, got {self.injection_score}")


@dataclass
class FetchResult:
    """Raw HTML fetch result with metadata"""
    html: str
    url: str
    status_code: int
    final_url: str
    headers: dict
    timing_ms: float


@dataclass
class ExtractionResult:
    """Extracted and cleaned HTML content"""
    html: str
    title: Optional[str]
    author: Optional[str]
    removed_tags: dict
    removed_hidden: int
    text_content: str


@dataclass
class InjectionAnalysis:
    """Security analysis result"""
    score: float
    flags: list[str]
    pattern_matches: list[dict]
    hidden_content_detected: bool
    imperative_density: float
