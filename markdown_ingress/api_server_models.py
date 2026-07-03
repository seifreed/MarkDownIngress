"""Request models for the FastAPI server."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from markdown_ingress.api_server_env import (
    APIServerEnvConfig,
    APIServerModelValidationConfig,
    load_api_server_env_config,
    load_api_server_model_validation_config,
)
from markdown_ingress.api_server_model_validators import (
    validate_api_screenshot_value as _validate_api_screenshot_value,
)
from markdown_ingress.api_server_model_validators import (
    validate_output_formats_value as _validate_output_formats_value,
)
from markdown_ingress.api_server_model_validators import (
    validate_policy_name_value as _validate_policy_name_value,
)
from markdown_ingress.api_server_model_validators import (
    validate_reports_dir_value as _validate_reports_dir_value,
)
from markdown_ingress.api_server_model_validators import (
    validate_url_no_ssrf as _validate_url_no_ssrf,
)
from markdown_ingress.api_server_response_models import (
    BatchIngestResponse,
    BatchJobAccepted,
    BatchJobResponse,
    ExtractorComparisonResponse,
    IngestResponse,
    JobStatus,
    SecurityReportResponse,
)
from markdown_ingress.config_models import (
    _validate_output_profile_name,
)
from markdown_ingress.config_validation import ChunkingStrategy, Mode, OutputFormat

_API_SERVER_ENV_CONFIG: APIServerEnvConfig = load_api_server_env_config()
_API_SERVER_MODEL_VALIDATION_CONFIG: APIServerModelValidationConfig = (
    load_api_server_model_validation_config()
)

MAX_BATCH_URLS = _API_SERVER_MODEL_VALIDATION_CONFIG.max_batch_urls
MAX_TIMEOUT_SECONDS = _API_SERVER_MODEL_VALIDATION_CONFIG.max_timeout_seconds
MAX_CHUNK_SIZE = _API_SERVER_MODEL_VALIDATION_CONFIG.max_chunk_size
# Each custom_pattern triggers a regex compile plus a ReDoS scan, and each
# domain policy is fully validated, so an uncapped list lets a small request
# body amplify into large CPU work. Cap both at the HTTP boundary.
MAX_CUSTOM_PATTERNS = _API_SERVER_MODEL_VALIDATION_CONFIG.max_custom_patterns
MAX_DOMAIN_POLICIES = _API_SERVER_MODEL_VALIDATION_CONFIG.max_domain_policies

__all__ = [
    "BatchIngestRequest",
    "BatchIngestResponse",
    "BatchJobAccepted",
    "BatchJobResponse",
    "DomainPolicyModel",
    "ExtractorComparisonResponse",
    "HTMLCompareRequest",
    "IngestRequest",
    "IngestResponse",
    "JobStatus",
    "RetryIngestRequest",
    "SecurityReportResponse",
]


def _allow_local_webhooks_enabled() -> bool:
    return load_api_server_env_config().allow_local_webhooks


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
    auto_render_threshold: int = Field(default=10, ge=1, le=5000)
    screenshot: bool | None = None
    extract_metadata: bool = Field(default=True)
    extract_links: bool = Field(default=True)
    advanced_security: bool = Field(default=False)
    use_llm: bool = Field(default=False)
    policy_name: str = Field(default="normal")
    custom_patterns: list[str] = Field(default_factory=list, max_length=MAX_CUSTOM_PATTERNS)
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
    domain_policies: list[DomainPolicyModel] = Field(
        default_factory=list, max_length=MAX_DOMAIN_POLICIES
    )

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


class HTMLCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    html: str = Field(..., min_length=1, max_length=2_000_000)
    model: str = Field(default="gpt-4")
