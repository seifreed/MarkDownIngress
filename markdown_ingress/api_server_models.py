"""Pydantic models and API type aliases for the FastAPI server."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator
from markdown_ingress.core.ssrf import validate_http_url_no_ssrf

Mode = Literal["auto", "fast", "render"]
ChunkingStrategy = Literal["none", "heading", "size"]
JobStatus = Literal["queued", "running", "completed", "failed"]
_logger = logging.getLogger(__name__)


def _read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _logger.warning("Invalid integer for %s=%r. Using default %d.", name, raw, default)
        return default
    if value < minimum:
        _logger.warning("Invalid value for %s=%r. Minimum is %d. Using default %d.", name, raw, minimum, default)
        return default
    return value


MAX_BATCH_URLS = _read_positive_int_env("MDI_API_MAX_BATCH_URLS", 100)
MAX_TIMEOUT_SECONDS = _read_positive_int_env("MDI_API_MAX_TIMEOUT", 300)
MAX_CHUNK_SIZE = _read_positive_int_env("MDI_API_MAX_CHUNK_SIZE", 20000)

def _validate_url_no_ssrf(url: str) -> str:
    """Validate URL against SSRF attacks."""
    return validate_http_url_no_ssrf(url)


class DomainPolicyModel(BaseModel):
    domain: str
    include_subdomains: bool = True
    mode: Mode | None = None
    timeout: float | None = Field(default=None, ge=1.0, le=300.0)
    auto_render_threshold: int | None = Field(default=None, ge=1, le=5000)
    strict: bool | None = None
    policy_name: str | None = None
    block_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    warn_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    request_interval: float | None = Field(default=None, ge=0.0, le=60.0)
    render_cost_budget: int | None = Field(default=None, ge=1, le=100)
    extract_metadata: bool | None = None
    extract_links: bool | None = None
    output_profile: str | None = None
    allowed_tags: list[str] = Field(default_factory=list)
    blocked_tags: list[str] = Field(default_factory=list)
    blocked_selectors: list[str] = Field(default_factory=list)
    unwrap_selectors: list[str] = Field(default_factory=list)
    notes: str | None = None


class IngestRequest(BaseModel):
    url: HttpUrl
    mode: Mode = Field(default="auto")
    strict: bool = Field(default=True)
    timeout: float = Field(default=30.0, ge=1, le=MAX_TIMEOUT_SECONDS)
    model: str = Field(default="gpt-4")
    stealth: bool = Field(default=False)
    disable_http2: bool = Field(default=False)
    extreme_mode: bool = Field(default=False)
    output_profile: str = Field(default="default")
    extract_blocks: bool = Field(default=False)
    chunking_strategy: ChunkingStrategy = Field(default="none")
    chunk_size: int = Field(default=1200, ge=100, le=MAX_CHUNK_SIZE)
    chunk_overlap: int = Field(default=120, ge=0, le=5000)
    auto_render_threshold: int = Field(default=50, ge=1, le=5000)
    screenshot: bool | str | None = None
    extract_metadata: bool = Field(default=True)
    extract_links: bool = Field(default=True)
    advanced_security: bool = Field(default=False)
    use_llm: bool = Field(default=False)
    policy_name: str = Field(default="normal")
    custom_patterns: list[str] = Field(default_factory=list)
    plugin_dirs: list[str] = Field(default_factory=list)
    detect_language: bool = Field(default=True)
    normalize_multilingual: bool = Field(default=True)
    include_security_explanation: bool = Field(default=True)
    include_observability: bool = Field(default=True)
    render_cost_budget: int | None = Field(default=None, ge=1, le=100)
    domain_policies: list[DomainPolicyModel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_url_ssrf(self):
        """Prevent SSRF attacks by blocking internal IPs and metadata endpoints."""
        _validate_url_no_ssrf(str(self.url))
        return self


class RetryIngestRequest(BaseModel):
    url: HttpUrl
    mode: Mode = Field(default="auto")
    strict: bool = Field(default=True)
    model: str = Field(default="gpt-4")
    max_retries: int = Field(default=3, ge=1, le=10)
    enable_stealth: bool = Field(default=True)
    initial_timeout: float = Field(default=60.0, ge=1.0, le=float(MAX_TIMEOUT_SECONDS))
    max_timeout: float = Field(default=float(MAX_TIMEOUT_SECONDS), ge=1.0, le=float(MAX_TIMEOUT_SECONDS))

    @model_validator(mode="after")
    def validate_timeout_bounds(self):
        if self.max_timeout < self.initial_timeout:
            raise ValueError("max_timeout must be greater than or equal to initial_timeout")
        return self

    @model_validator(mode="after")
    def validate_url_ssrf(self):
        """Prevent SSRF attacks by blocking internal IPs and metadata endpoints."""
        _validate_url_no_ssrf(str(self.url))
        return self


class BatchIngestRequest(BaseModel):
    urls: list[HttpUrl] = Field(..., max_length=MAX_BATCH_URLS)
    mode: Mode = Field(default="auto")
    strict: bool = Field(default=True)
    timeout: float = Field(default=30.0, ge=1, le=MAX_TIMEOUT_SECONDS)
    model: str = Field(default="gpt-4")
    auto_render_threshold: int = Field(default=50, ge=1, le=5000)
    stealth: bool = Field(default=False)
    disable_http2: bool = Field(default=False)
    extreme_mode: bool = Field(default=False)
    screenshot: bool | str | None = None
    extract_metadata: bool = Field(default=True)
    extract_links: bool = Field(default=True)
    advanced_security: bool = Field(default=False)
    use_llm: bool = Field(default=False)
    policy_name: str = Field(default="normal")
    custom_patterns: list[str] = Field(default_factory=list)
    plugin_dirs: list[str] = Field(default_factory=list)
    output_profile: str = Field(default="default")
    extract_blocks: bool = Field(default=False)
    chunking_strategy: ChunkingStrategy = Field(default="none")
    chunk_size: int = Field(default=1200, ge=100, le=MAX_CHUNK_SIZE)
    chunk_overlap: int = Field(default=120, ge=0, le=5000)
    detect_language: bool = Field(default=True)
    normalize_multilingual: bool = Field(default=True)
    include_security_explanation: bool = Field(default=True)
    include_observability: bool = Field(default=True)
    render_cost_budget: int | None = Field(default=None, ge=1, le=100)
    domain_policies: list[DomainPolicyModel] = Field(default_factory=list)
    max_concurrent: int = Field(default=5, ge=1, le=64)
    webhook_url: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_urls_ssrf(self):
        """Prevent SSRF attacks by blocking internal IPs and metadata endpoints."""
        for url in self.urls:
            _validate_url_no_ssrf(str(url))
        if self.webhook_url is not None:
            _validate_url_no_ssrf(str(self.webhook_url))
        return self


class IngestResponse(BaseModel):
    markdown: str
    metadata: dict[str, Any]
    token_estimate: int
    injection_score: float
    flags: list[str]
    content_hash: str
    removed_elements: dict[str, Any]
    structured_blocks: list[dict[str, Any]] | None = None
    chunks: list[dict[str, Any]] | None = None
    security_explanation: dict[str, Any] | None = None
    observability: dict[str, Any] | None = None


class BatchIngestResponse(BaseModel):
    results: list[dict[str, Any]]
    success_count: int
    failure_count: int


class SecurityReportResponse(BaseModel):
    injection_score: float
    risk_level: str
    pattern_matches: list[dict[str, Any]]
    flags: list[str]
    hidden_content_detected: bool
    hidden_elements_count: int
    imperative_density: float
    url: str
    title: str
    token_estimate: int
    token_reduction_percent: float
    original_size_bytes: int
    cleaned_size_bytes: int
    content_hash: str
    structural_hash: str
    removed_elements: dict[str, Any]
    language: str | None = None
    explanation: dict[str, Any] = Field(default_factory=dict)
    observability: dict[str, Any] = Field(default_factory=dict)


class BatchJobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str
    poll_url: str
    expires_in_seconds: int
    ttl_applies_to: Literal["completed_jobs"] = "completed_jobs"


class BatchJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class HTMLCompareRequest(BaseModel):
    html: str = Field(..., min_length=1, max_length=2_000_000)
    model: str = Field(default="gpt-4")


class ExtractorComparisonResponse(BaseModel):
    results: dict[str, dict[str, Any]]
