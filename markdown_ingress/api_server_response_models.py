"""Response models for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "completed", "failed"]


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


class ExtractorComparisonResponse(BaseModel):
    results: dict[str, dict[str, Any]]
