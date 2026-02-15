"""
Data models for MarkDownIngress
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime, timezone
import json


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
    metadata: dict = field(default_factory=dict)


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


@dataclass
class SecurityReport:
    """
    Comprehensive security analysis report with detailed metadata.
    Can be exported to JSON for auditing and analysis.
    """
    # Core metrics
    injection_score: float
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    
    # Detection details
    pattern_matches: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    
    # Content analysis
    hidden_content_detected: bool = False
    hidden_elements_count: int = 0
    imperative_density: float = 0.0
    
    # Metadata
    url: str = ""
    title: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: str = "0.4.0"
    
    # Token analysis
    token_estimate: int = 0
    token_reduction_percent: float = 0.0
    original_size_bytes: int = 0
    cleaned_size_bytes: int = 0
    
    # Hashing
    content_hash: str = ""
    structural_hash: str = ""
    
    # Removal summary
    removed_elements: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    def to_json(self, indent: int = 2) -> str:
        """Export as formatted JSON string"""
        return json.dumps(self.to_dict(), indent=indent)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SecurityReport':
        """Create SecurityReport from dictionary"""
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'SecurityReport':
        """Create SecurityReport from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def save(self, filepath: str):
        """Save report to JSON file"""
        with open(filepath, 'w') as f:
            f.write(self.to_json())
    
    @classmethod
    def load(cls, filepath: str) -> 'SecurityReport':
        """Load report from JSON file"""
        with open(filepath, 'r') as f:
            return cls.from_json(f.read())
