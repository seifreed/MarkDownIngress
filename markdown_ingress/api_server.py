"""
FastAPI server for MarkDownIngress
Provides REST API endpoints for web content ingestion
"""

from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from markdown_ingress.api import generate_security_report, ingest, retry_ingest

app = FastAPI(
    title="MarkDownIngress API",
    description="Deterministic Web → Markdown Engine for LLM Pipelines",
    version = "0.6.0",
)


class IngestRequest(BaseModel):
    """Request model for single URL ingestion"""

    url: HttpUrl
    mode: str = Field(default="auto", pattern="^(auto|fast|render)$")
    strict: bool = Field(default=True, description="Enable strict security mode")
    timeout: int = Field(default=60, ge=1, le=300, description="Request timeout in seconds")
    model: str = Field(default="gpt-4", description="LLM model for token estimation")
    stealth: bool = Field(default=False, description="Enable stealth mode (render mode only)")
    disable_http2: bool = Field(default=False, description="Disable HTTP/2 protocol")
    extreme_mode: bool = Field(default=False, description="Enable extreme timeouts")


class RetryIngestRequest(BaseModel):
    """Request model for ingestion with retry logic"""

    url: HttpUrl
    mode: str = Field(default="auto", pattern="^(auto|fast|render)$")
    strict: bool = Field(default=True)
    model: str = Field(default="gpt-4")
    max_retries: int = Field(default=3, ge=1, le=10)
    enable_stealth: bool = Field(default=True)
    initial_timeout: float = Field(default=60.0, ge=1.0, le=300.0)


class BatchIngestRequest(BaseModel):
    """Request model for batch URL ingestion"""

    urls: list[HttpUrl] = Field(..., max_length=100)
    mode: str = Field(default="auto", pattern="^(auto|fast|render)$")
    strict: bool = Field(default=True)
    timeout: int = Field(default=60, ge=1, le=300)
    model: str = Field(default="gpt-4")


class IngestResponse(BaseModel):
    """Response model for ingestion"""

    markdown: str
    metadata: dict[str, Any]
    token_estimate: int
    injection_score: float
    flags: list[str]
    content_hash: str
    removed_elements: dict[str, Any]


class BatchIngestResponse(BaseModel):
    """Response model for batch ingestion"""

    results: list[dict[str, Any]]
    success_count: int
    failure_count: int


class SecurityReportResponse(BaseModel):
    """Response model for security report"""

    injection_score: float
    risk_level: str
    flags: list[str]
    hidden_content_detected: bool
    hidden_elements_count: int
    url: str
    title: str
    token_estimate: int
    token_reduction_percent: float
    content_hash: str
    structural_hash: str
    removed_elements: dict[str, Any]


@app.post("/ingest", response_model=IngestResponse)
async def ingest_endpoint(request: IngestRequest):
    """
    Ingest a single URL and return structured markdown

    **Example:**
    ```json
    {
        "url": "https://example.com",
        "mode": "auto",
        "strict": true,
        "timeout": 60
    }
    ```
    """
    try:
        doc = ingest(
            url=str(request.url),
            mode=request.mode,
            strict=request.strict,
            timeout=float(request.timeout),
            model=request.model,
            stealth=request.stealth,
            disable_http2=request.disable_http2,
            extreme_mode=request.extreme_mode,
        )
        return IngestResponse(
            markdown=doc.markdown,
            metadata=doc.metadata,
            token_estimate=doc.token_estimate,
            injection_score=doc.injection_score,
            flags=doc.flags,
            content_hash=doc.content_hash,
            removed_elements=doc.removed_elements,
        )
    except ImportError as e:
        raise HTTPException(status_code=400, detail=f"Render mode requires Playwright: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/retry", response_model=IngestResponse)
async def retry_ingest_endpoint(request: RetryIngestRequest):
    """
    Ingest with automatic retry logic and timeout escalation

    **Example:**
    ```json
    {
        "url": "https://example.com",
        "mode": "auto",
        "max_retries": 3,
        "enable_stealth": true,
        "initial_timeout": 60.0
    }
    ```
    """
    try:
        doc = retry_ingest(
            url=str(request.url),
            mode=request.mode,
            strict=request.strict,
            model=request.model,
            max_retries=request.max_retries,
            enable_stealth=request.enable_stealth,
            initial_timeout=request.initial_timeout,
        )
        return IngestResponse(
            markdown=doc.markdown,
            metadata=doc.metadata,
            token_estimate=doc.token_estimate,
            injection_score=doc.injection_score,
            flags=doc.flags,
            content_hash=doc.content_hash,
            removed_elements=doc.removed_elements,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/batch", response_model=BatchIngestResponse)
async def batch_ingest_endpoint(request: BatchIngestRequest):
    """
    Ingest multiple URLs in batch

    **Example:**
    ```json
    {
        "urls": [
            "https://example.com",
            "https://example.org"
        ],
        "mode": "auto",
        "timeout": 60
    }
    ```
    """
    results = []
    success_count = 0
    failure_count = 0

    for url in request.urls:
        try:
            doc = ingest(
                url=str(url),
                mode=request.mode,
                strict=request.strict,
                timeout=float(request.timeout),
                model=request.model,
            )
            results.append(
                {
                    "url": str(url),
                    "success": True,
                    "data": {
                        "markdown": doc.markdown,
                        "metadata": doc.metadata,
                        "token_estimate": doc.token_estimate,
                        "injection_score": doc.injection_score,
                        "flags": doc.flags,
                        "content_hash": doc.content_hash,
                        "removed_elements": doc.removed_elements,
                    },
                }
            )
            success_count += 1
        except Exception as e:
            results.append({"url": str(url), "success": False, "error": str(e)})
            failure_count += 1

    return BatchIngestResponse(
        results=results, success_count=success_count, failure_count=failure_count
    )


@app.post("/security/report", response_model=SecurityReportResponse)
async def security_report_endpoint(request: IngestRequest):
    """
    Generate comprehensive security report for a URL

    **Example:**
    ```json
    {
        "url": "https://example.com",
        "mode": "auto",
        "strict": true,
        "timeout": 60
    }
    ```
    """
    try:
        report = generate_security_report(
            url=str(request.url),
            mode=request.mode,
            strict=request.strict,
            model=request.model,
            timeout=float(request.timeout),
        )
        return SecurityReportResponse(
            injection_score=report.injection_score,
            risk_level=report.risk_level,
            flags=report.flags,
            hidden_content_detected=report.hidden_content_detected,
            hidden_elements_count=report.hidden_elements_count,
            url=report.url,
            title=report.title,
            token_estimate=report.token_estimate,
            token_reduction_percent=report.token_reduction_percent,
            content_hash=report.content_hash,
            structural_hash=report.structural_hash,
            removed_elements=report.removed_elements,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "version": "0.6.0", "service": "MarkDownIngress API"}


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "MarkDownIngress API - See /docs for interactive documentation",
        "version": "0.6.0",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "ingest": "/ingest",
            "retry_ingest": "/ingest/retry",
            "batch_ingest": "/ingest/batch",
            "security_report": "/security/report",
        },
    }


def main():
    """Run the server"""
    uvicorn.run("markdown_ingress.api_server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
