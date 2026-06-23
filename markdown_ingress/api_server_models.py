"""Pydantic models and API type aliases for the FastAPI server."""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from markdown_ingress.api_server_env import _read_bool_env, _read_positive_int_env
from markdown_ingress.config_models import (
    VALID_OUTPUT_REPRESENTATIONS,
    VALID_POLICY_NAMES,
    _validate_output_profile_name,
)
from markdown_ingress.config_validation import ChunkingStrategy, Mode, OutputFormat
from markdown_ingress.core.ssrf import (
    resolve_allow_local_urls,
    validate_http_url_no_ssrf,
    validate_http_url_no_ssrf_with_dns_check,
)

JobStatus = Literal["queued", "running", "completed", "failed"]
_logger = logging.getLogger(__name__)

MAX_BATCH_URLS = _read_positive_int_env("MDI_API_MAX_BATCH_URLS", 100)
MAX_TIMEOUT_SECONDS = _read_positive_int_env("MDI_API_MAX_TIMEOUT", 300)
MAX_CHUNK_SIZE = _read_positive_int_env("MDI_API_MAX_CHUNK_SIZE", 20000)


def _allow_local_webhooks_enabled() -> bool:
    return _read_bool_env("MDI_API_ALLOW_LOCAL_WEBHOOKS", False)


def _validate_url_no_ssrf(
    url: str,
    *,
    allow_local: bool | None = None,
    resolve_dns: bool = True,
) -> str:
    """Validate URL against SSRF attacks."""
    resolved_allow_local = resolve_allow_local_urls(allow_local)
    if resolve_dns:
        return validate_http_url_no_ssrf_with_dns_check(url, allow_local=resolved_allow_local)
    return validate_http_url_no_ssrf(
        url,
        allow_local=resolved_allow_local,
        resolve_dns=False,
    )


def _validate_output_formats_value(value: list[str]) -> list[str]:
    """Validate supported document output representations for HTTP requests."""
    if not value:
        raise ValueError("output_formats must be a non-empty list of supported format strings")
    for index, item in enumerate(value):
        if item not in VALID_OUTPUT_REPRESENTATIONS:
            raise ValueError(
                f"output_formats[{index}] has invalid value '{item}'. "
                f"Must be one of: {', '.join(VALID_OUTPUT_REPRESENTATIONS)}"
            )
    return value


def _validate_policy_name_value(value: str | None) -> str | None:
    """Validate supported policy names for HTTP requests."""
    if value is None:
        return None
    if value not in VALID_POLICY_NAMES:
        raise ValueError(
            f"policy_name has invalid value '{value}'. "
            f"Must be one of: {', '.join(VALID_POLICY_NAMES)}"
        )
    return value


def _validate_reports_dir_value(value: str) -> str:
    """Validate that reports_dir is non-empty and safe from path traversal."""
    if not value.strip():
        raise ValueError("reports_dir cannot be empty")
    if "\x00" in value:
        raise ValueError("reports_dir contains null byte")
    if (
        Path(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise ValueError("reports_dir must be relative")
    if ".." in value.split("/") or ".." in value.split("\\"):
        raise ValueError("reports_dir must not contain '..' path segments")
    return value


def _validate_api_screenshot_value(value: Any) -> bool | None:
    """Restrict HTTP API screenshot requests to server-managed captures."""
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("screenshot must be a boolean or null in the HTTP API")


class DomainPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

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

    @field_validator("policy_name")
    @classmethod
    def validate_policy_name(cls, value: str | None) -> str | None:
        return _validate_policy_name_value(value)

    @field_validator("output_profile")
    @classmethod
    def validate_output_profile(cls, value: str | None) -> str | None:
        return _validate_output_profile_name(value)


class _IngestParams(BaseModel):
    """Shared ingestion parameters for single and batch HTTP requests."""

    model_config = ConfigDict(extra="forbid", strict=True)

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
    screenshot: bool | None = None
    extract_metadata: bool = Field(default=True)
    extract_links: bool = Field(default=True)
    advanced_security: bool = Field(default=False)
    use_llm: bool = Field(default=False)
    policy_name: str = Field(default="normal")
    custom_patterns: list[str] = Field(default_factory=list)
    output_format: OutputFormat = Field(default="text")
    output_formats: list[str] = Field(default_factory=lambda: ["markdown"])
    detect_language: bool = Field(default=True)
    normalize_multilingual: bool = Field(default=True)
    include_security_explanation: bool = Field(default=True)
    include_observability: bool = Field(default=True)
    save_reports: bool = Field(default=False)
    reports_dir: str = Field(default="reports")
    fetcher_user_agent: str = Field(default="")
    domain_request_interval: float = Field(default=0.25, ge=0.0, le=60.0)
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=100)
    circuit_breaker_open_seconds: float = Field(default=30.0, ge=0.1, le=3600.0)
    render_cost_budget: int | None = Field(default=None, ge=1, le=100)
    domain_policies: list[DomainPolicyModel] = Field(default_factory=list)

    @field_validator("output_formats")
    @classmethod
    def validate_output_formats(cls, value: list[str]) -> list[str]:
        return _validate_output_formats_value(value)

    @field_validator("screenshot", mode="before")
    @classmethod
    def validate_screenshot(cls, value: Any) -> bool | None:
        return _validate_api_screenshot_value(value)

    @field_validator("policy_name")
    @classmethod
    def validate_policy_name(cls, value: str) -> str:
        return _validate_policy_name_value(value) or "normal"

    @field_validator("output_profile")
    @classmethod
    def validate_output_profile(cls, value: str) -> str:
        return _validate_output_profile_name(value) or "default"

    @field_validator("reports_dir")
    @classmethod
    def validate_reports_dir(cls, value: str) -> str:
        return _validate_reports_dir_value(value)


class IngestRequest(_IngestParams):
    url: HttpUrl

    @model_validator(mode="after")
    def validate_url_ssrf(self):
        """Prevent SSRF attacks by blocking internal IPs and metadata endpoints."""
        _validate_url_no_ssrf(str(self.url))
        return self


class RetryIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: HttpUrl
    mode: Mode = Field(default="auto")
    strict: bool = Field(default=True)
    model: str = Field(default="gpt-4")
    max_retries: int = Field(default=3, ge=1, le=10)
    enable_stealth: bool = Field(default=True)
    initial_timeout: float = Field(default=60.0, ge=1.0, le=float(MAX_TIMEOUT_SECONDS))
    max_timeout: float = Field(
        default=float(MAX_TIMEOUT_SECONDS), ge=1.0, le=float(MAX_TIMEOUT_SECONDS)
    )

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


class BatchIngestRequest(_IngestParams):
    urls: list[HttpUrl] = Field(..., max_length=MAX_BATCH_URLS)
    max_concurrent: int = Field(default=5, ge=1, le=64)
    webhook_url: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_urls_ssrf(self):
        """Prevent SSRF attacks by blocking internal IPs and metadata endpoints."""
        for url in self.urls:
            _validate_url_no_ssrf(str(url), resolve_dns=False)
        if self.webhook_url is not None:
            _validate_url_no_ssrf(
                str(self.webhook_url),
                allow_local=_allow_local_webhooks_enabled(),
                resolve_dns=False,
            )
        return self


class IngestResponse(BaseModel):
    markdown: str
    metadata: dict[str, Any]
    token_estimate: int
    injection_score: float
    flags: list[str]
    content_hash: str
    removed_elements: dict[str, Any]
    screenshot_path: str | None = None
    enriched_metadata: dict[str, Any] | None = None
    links: dict[str, Any] | None = None
    nova_score: float | None = None
    nova_details: dict[str, Any] | None = None
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
    timestamp: str
    version: str
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
    model_config = ConfigDict(extra="forbid", strict=True)

    html: str = Field(..., min_length=1, max_length=2_000_000)
    model: str = Field(default="gpt-4")


class ExtractorComparisonResponse(BaseModel):
    results: dict[str, dict[str, Any]]
