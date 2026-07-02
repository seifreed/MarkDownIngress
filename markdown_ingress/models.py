"""
Data models for MarkDownIngress
"""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from markdown_ingress.config_validation import ensure_bool as _ensure_bool
from markdown_ingress.config_validation import ensure_score as _ensure_score
from markdown_ingress.models_headers import CaseInsensitiveHeaders
from markdown_ingress.models_validation import (
    _ensure_dict,
    _ensure_dict_list,
    _ensure_finite_float_metric,
    _ensure_iso_datetime_str,
    _ensure_non_negative_int_list,
    _ensure_non_negative_int_metric,
    _ensure_optional_dict,
    _ensure_optional_dict_list,
    _ensure_optional_str,
    _ensure_percentage,
    _ensure_str,
    _ensure_str_list,
)


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
        self.markdown = _ensure_str("markdown", self.markdown)
        self.metadata = _ensure_dict("metadata", self.metadata)
        self.content_hash = _ensure_str("content_hash", self.content_hash)
        self.injection_score = _ensure_score("injection_score", self.injection_score)
        self.token_estimate = _ensure_non_negative_int_metric("token_estimate", self.token_estimate)
        self.flags = _ensure_str_list("flags", self.flags)
        self.removed_elements = _ensure_dict("removed_elements", self.removed_elements)
        self.screenshot_path = _ensure_optional_str("screenshot_path", self.screenshot_path)
        self.enriched_metadata = _ensure_optional_dict("enriched_metadata", self.enriched_metadata)
        self.links = _ensure_optional_dict("links", self.links)
        self.nova_score = (
            None if self.nova_score is None else _ensure_score("nova_score", self.nova_score)
        )
        self.nova_details = _ensure_optional_dict("nova_details", self.nova_details)
        self.structured_blocks = _ensure_optional_dict_list(
            "structured_blocks", self.structured_blocks
        )
        self.chunks = _ensure_optional_dict_list("chunks", self.chunks)
        self.security_explanation = _ensure_optional_dict(
            "security_explanation", self.security_explanation
        )
        self.observability = _ensure_optional_dict("observability", self.observability)

    def to_serializable_dict(self) -> dict:
        """Ordered JSON-serializable field map shared by cache and CLI serializers."""
        return {
            "markdown": self.markdown,
            "metadata": self.metadata,
            "token_estimate": self.token_estimate,
            "content_hash": self.content_hash,
            "injection_score": self.injection_score,
            "flags": self.flags,
            "removed_elements": self.removed_elements,
            "screenshot_path": self.screenshot_path,
            "enriched_metadata": self.enriched_metadata,
            "links": self.links,
            "nova_score": self.nova_score,
            "nova_details": self.nova_details,
            "structured_blocks": self.structured_blocks,
            "chunks": self.chunks,
            "security_explanation": self.security_explanation,
            "observability": self.observability,
        }


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
        self.html = _ensure_str("html", self.html)
        self.url = _ensure_str("url", self.url)
        self.status_code = _ensure_non_negative_int_metric("status_code", self.status_code)
        if not 100 <= self.status_code <= 599:
            raise ValueError(f"status_code must be between 100 and 599, got {self.status_code}")
        self.final_url = _ensure_str("final_url", self.final_url)
        self.timing_ms = _ensure_finite_float_metric("timing_ms", self.timing_ms)
        if self.timing_ms < 0.0:
            raise ValueError(f"timing_ms must be non-negative, got {self.timing_ms}")
        self.metadata = _ensure_dict("metadata", self.metadata)
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

    def __post_init__(self) -> None:
        self.html = _ensure_str("html", self.html)
        self.title = _ensure_optional_str("title", self.title)
        self.author = _ensure_optional_str("author", self.author)
        self.removed_tags = _ensure_dict("removed_tags", self.removed_tags)
        self.removed_hidden = _ensure_non_negative_int_metric("removed_hidden", self.removed_hidden)
        self.text_content = _ensure_str("text_content", self.text_content)


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

    def __post_init__(self) -> None:
        self.block_type = _ensure_str("block_type", self.block_type)
        self.text = _ensure_str("text", self.text)
        self.markdown = _ensure_str("markdown", self.markdown)
        self.ordinal = _ensure_non_negative_int_metric("ordinal", self.ordinal)
        if self.level is not None:
            self.level = _ensure_non_negative_int_metric("level", self.level)
        self.structural_hash = _ensure_str("structural_hash", self.structural_hash)
        self.metadata = _ensure_dict("metadata", self.metadata)


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

    def __post_init__(self) -> None:
        self.chunk_id = _ensure_str("chunk_id", self.chunk_id)
        self.text = _ensure_str("text", self.text)
        self.markdown = _ensure_str("markdown", self.markdown)
        self.block_ordinals = _ensure_non_negative_int_list("block_ordinals", self.block_ordinals)
        self.structural_hash = _ensure_str("structural_hash", self.structural_hash)
        self.token_estimate = _ensure_non_negative_int_metric("token_estimate", self.token_estimate)
        self.char_start = _ensure_non_negative_int_metric("char_start", self.char_start)
        self.char_end = _ensure_non_negative_int_metric("char_end", self.char_end)
        if self.char_end < self.char_start:
            raise ValueError(
                f"char_end must be greater than or equal to char_start, got {self.char_end}"
            )
        self.metadata = _ensure_dict("metadata", self.metadata)


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
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def load(cls, filepath: str) -> "SecurityReport":
        """Load report from JSON file"""
        with open(filepath, encoding="utf-8") as f:
            return cls.from_json(f.read())
