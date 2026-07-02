"""Security report data model."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from markdown_ingress.config_validation import ensure_bool as _ensure_bool
from markdown_ingress.config_validation import ensure_score as _ensure_score
from markdown_ingress.models_validation import (
    _ensure_dict,
    _ensure_dict_list,
    _ensure_finite_float_metric,
    _ensure_iso_datetime_str,
    _ensure_non_negative_int_metric,
    _ensure_optional_str,
    _ensure_percentage,
    _ensure_str,
    _ensure_str_list,
)


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
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: str = "1.0.0"

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
    language: str | None = None
    explanation: dict = field(default_factory=dict)
    observability: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate report metrics after construction or JSON loading."""
        self.risk_level = _ensure_str("risk_level", self.risk_level)
        self.pattern_matches = _ensure_dict_list("pattern_matches", self.pattern_matches)
        self.flags = _ensure_str_list("flags", self.flags)
        self.hidden_content_detected = _ensure_bool(
            "hidden_content_detected", self.hidden_content_detected
        )
        self.url = _ensure_str("url", self.url)
        self.title = _ensure_str("title", self.title)
        self.timestamp = _ensure_iso_datetime_str("timestamp", self.timestamp)
        self.version = _ensure_str("version", self.version)
        self.content_hash = _ensure_str("content_hash", self.content_hash)
        self.structural_hash = _ensure_str("structural_hash", self.structural_hash)
        self.removed_elements = _ensure_dict("removed_elements", self.removed_elements)
        self.language = _ensure_optional_str("language", self.language)
        self.explanation = _ensure_dict("explanation", self.explanation)
        self.observability = _ensure_dict("observability", self.observability)
        self.injection_score = _ensure_score("injection_score", self.injection_score)
        self.hidden_elements_count = _ensure_non_negative_int_metric(
            "hidden_elements_count", self.hidden_elements_count
        )
        self.imperative_density = _ensure_finite_float_metric(
            "imperative_density", self.imperative_density
        )
        if self.imperative_density < 0.0:
            raise ValueError(
                f"imperative_density must be non-negative, got {self.imperative_density}"
            )
        self.token_estimate = _ensure_non_negative_int_metric("token_estimate", self.token_estimate)
        self.token_reduction_percent = _ensure_percentage(
            "token_reduction_percent", self.token_reduction_percent
        )
        self.original_size_bytes = _ensure_non_negative_int_metric(
            "original_size_bytes", self.original_size_bytes
        )
        self.cleaned_size_bytes = _ensure_non_negative_int_metric(
            "cleaned_size_bytes", self.cleaned_size_bytes
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Export as formatted JSON string"""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> SecurityReport:
        """Create SecurityReport from dictionary"""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> SecurityReport:
        """Create SecurityReport from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def save(self, filepath: str):
        """Save report to JSON file"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def load(cls, filepath: str) -> SecurityReport:
        """Load report from JSON file"""
        with open(filepath, encoding="utf-8") as f:
            return cls.from_json(f.read())
