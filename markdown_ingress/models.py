"""
Data models for MarkDownIngress
"""

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

_MISSING = object()


def _ensure_int_metric(field_name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int, got {type(value).__name__}")
    return value


def _ensure_non_negative_int_metric(field_name: str, value: object) -> int:
    metric = _ensure_int_metric(field_name, value)
    if metric < 0:
        raise ValueError(f"{field_name} must be non-negative, got {metric}")
    return metric


def _ensure_finite_float_metric(field_name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {type(value).__name__}")
    metric = float(value)
    if not math.isfinite(metric):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return metric


def _ensure_score(field_name: str, value: object) -> float:
    score = _ensure_finite_float_metric(field_name, value)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{field_name} must be between 0.0 and 1.0, got {score}")
    return score


class CaseInsensitiveHeaders(dict[str, str]):
    """Dictionary-like header container with case-insensitive string lookups."""

    @staticmethod
    def _normalize_key(key: str) -> str:
        return key.lower()

    def __init__(self, initial: Mapping[str, str] | None = None, **kwargs: str) -> None:
        super().__init__()
        if initial:
            self.update(initial)
        if kwargs:
            self.update(kwargs)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return super().__contains__(self._normalize_key(key))

    def __getitem__(self, key: str) -> str:
        return super().__getitem__(self._normalize_key(key))

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(self._normalize_key(key), value)

    def __delitem__(self, key: str) -> None:
        super().__delitem__(self._normalize_key(key))

    def get(self, key: object, default: Any = None) -> Any:
        if not isinstance(key, str):
            return default
        return super().get(self._normalize_key(key), default)

    def pop(self, key: object, default: Any = _MISSING) -> Any:
        if not isinstance(key, str):
            if default is _MISSING:
                raise KeyError(key)
            return default
        normalized = self._normalize_key(key)
        if default is _MISSING:
            return super().pop(normalized)
        return super().pop(normalized, default)

    def setdefault(self, key: str, default: str = "") -> str:
        normalized = self._normalize_key(key)
        if normalized in self:
            return super().__getitem__(normalized)
        super().__setitem__(normalized, default)
        return default

    def copy(self) -> "CaseInsensitiveHeaders":
        return type(self)(self)

    def update(
        self,
        *args: Any,
        **kwargs: str,
    ) -> None:
        if len(args) > 1:
            raise TypeError(f"update expected at most 1 argument, got {len(args)}")
        other = args[0] if args else None
        if other:
            items = other.items() if isinstance(other, Mapping) else other
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value


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
    screenshot_path: str | None = None
    enriched_metadata: dict | None = None
    links: dict | None = None
    # Nova-tracer integration (v0.8.0)
    nova_score: float | None = None
    nova_details: dict | None = None
    structured_blocks: list[dict] | None = None
    chunks: list[dict] | None = None
    security_explanation: dict | None = None
    observability: dict | None = None

    def __post_init__(self):
        """Validate field constraints."""
        self.injection_score = _ensure_score("injection_score", self.injection_score)
        self.token_estimate = _ensure_non_negative_int_metric("token_estimate", self.token_estimate)


@dataclass
class FetchResult:
    """Raw HTML fetch result with metadata"""

    html: str
    url: str
    status_code: int
    final_url: str
    headers: Mapping[str, str] | CaseInsensitiveHeaders
    timing_ms: float
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.headers, CaseInsensitiveHeaders):
            self.headers = CaseInsensitiveHeaders(dict(self.headers))


@dataclass
class ExtractionResult:
    """Extracted and cleaned HTML content"""

    html: str
    title: str | None
    author: str | None
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
class StructuredBlock:
    """Structured representation of a logical document block."""

    block_type: str
    text: str
    markdown: str
    ordinal: int
    level: int | None = None
    structural_hash: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for JSON-compatible APIs."""
        return asdict(self)


@dataclass
class DocumentChunk:
    """Stable chunk emitted from structured blocks."""

    chunk_id: str
    text: str
    markdown: str
    block_ordinals: list[int]
    structural_hash: str
    token_estimate: int
    char_start: int
    char_end: int
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for JSON-compatible APIs."""
        return asdict(self)


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
    version: str = "0.8.0"

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
        self.token_reduction_percent = _ensure_finite_float_metric(
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
    def from_dict(cls, data: dict) -> "SecurityReport":
        """Create SecurityReport from dictionary"""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "SecurityReport":
        """Create SecurityReport from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def save(self, filepath: str):
        """Save report to JSON file"""
        with open(filepath, "w") as f:
            f.write(self.to_json())

    @classmethod
    def load(cls, filepath: str) -> "SecurityReport":
        """Load report from JSON file"""
        with open(filepath) as f:
            return cls.from_json(f.read())
