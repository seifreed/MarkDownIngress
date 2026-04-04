from pathlib import Path

from fastapi.testclient import TestClient

from markdown_ingress import DomainPolicy, IngestConfig, compare_extractors, ingest
from markdown_ingress.core.config import Config
import markdown_ingress.api_server as api_server
from markdown_ingress.api_server import app
from markdown_ingress.models import FetchResult


FIXTURE_HTML = Path("tests/fixtures/technical_doc.html").read_text(encoding="utf-8")


def _make_fetch_result(url: str, html: str) -> FetchResult:
    return FetchResult(
        html=html,
        url=url,
        status_code=200,
        final_url=url,
        headers={"content-type": "text/html"},
        timing_ms=5.0,
        metadata={},
    )


def test_output_profile_produces_blocks_chunks_and_language(monkeypatch):
    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.Fetcher.fetch_sync",
        lambda self, url: _make_fetch_result(url, FIXTURE_HTML),
    )

    doc = ingest(
        "https://docs.example.com/guide",
        mode="fast",
        output_profile="rag_chunkable",
    )

    assert doc.structured_blocks is not None
    assert doc.chunks is not None
    assert any(block["block_type"] == "table" for block in doc.structured_blocks)
    assert any(block["block_type"] == "code" for block in doc.structured_blocks)
    assert doc.metadata["language"] == "en"
    assert doc.metadata["output_profile"] == "rag_chunkable"
    assert "stage_timings_ms" in doc.metadata
    assert doc.security_explanation is not None


def test_domain_policy_overrides_timeout_policy_and_profile(monkeypatch):
    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.Fetcher.fetch_sync",
        lambda self, url: _make_fetch_result(url, FIXTURE_HTML),
    )

    config = IngestConfig(
        mode="auto",
        timeout=30.0,
        output_profile="default",
        domain_policies=[
            DomainPolicy(
                domain="docs.example.com",
                timeout=5.0,
                policy_name="strict",
                output_profile="llm_safe",
            )
        ],
    )

    doc = ingest("https://docs.example.com/page", config=config)

    assert doc.metadata["policy"] == "strict"
    assert doc.metadata["output_profile"] == "llm_safe"
    assert doc.metadata["domain_policy"]["domain"] == "docs.example.com"


def test_domain_policy_output_profile_replaces_base_profile_defaults():
    config = IngestConfig(
        output_profile="for_search",
        domain_policies=[
            DomainPolicy(
                domain="example.com",
                output_profile="rag_chunkable",
            )
        ],
    )

    resolved, matched = config.resolve_for_url("https://example.com/page")

    assert matched is not None
    assert resolved.output_profile == "rag_chunkable"
    assert resolved.mode == "auto"
    assert resolved.strict is True
    assert resolved.chunking_strategy == "heading"
    assert resolved.chunk_size == 900
    assert resolved.output_formats == ["markdown", "blocks", "chunks"]


def test_output_profile_preserves_explicit_values_even_when_equal_to_defaults():
    rag = IngestConfig(output_profile="rag_chunkable", extract_blocks=False).apply_output_profile()
    search = IngestConfig(output_profile="for_search", mode="auto").apply_output_profile()

    assert rag.extract_blocks is False
    assert search.mode == "auto"


def test_legacy_config_preserves_explicit_default_like_output_overrides():
    runtime = Config(
        output_profile="rag_chunkable",
        extract_blocks=False,
        output_format="json",
    ).to_ingest_config()

    resolved = runtime.apply_output_profile()

    assert runtime.output_format == "json"
    assert "extract_blocks" in runtime.explicit_keys()
    assert "output_format" in runtime.explicit_keys()
    assert resolved.extract_blocks is False


def test_markdown_preserves_code_fences_and_tables(monkeypatch):
    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.Fetcher.fetch_sync",
        lambda self, url: _make_fetch_result(url, FIXTURE_HTML),
    )

    doc = ingest("https://docs.example.com/markdown", mode="fast", extract_blocks=True)

    assert "```python" in doc.markdown
    assert "| Field | Description |" in doc.markdown


def test_compare_extractors_returns_readability_baseline():
    result = compare_extractors(FIXTURE_HTML)

    assert "readability" in result
    assert result["readability"]["length"] > 0
    assert "trafilatura" in result
    assert "heading_count" in result["readability"]


def test_versioned_api_and_batch_jobs(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.Fetcher.fetch_sync",
        lambda self, url: _make_fetch_result(url, FIXTURE_HTML),
    )
    monkeypatch.setattr(
        "markdown_ingress.api_server.ingest",
        lambda **kwargs: ingest(
            "https://docs.example.com/api",
            mode="fast",
            output_profile="rag_chunkable",
            extract_blocks=True,
            chunking_strategy="heading",
            chunk_size=500,
        ),
    )

    response = client.post(
        "/api/v1/ingest",
        json={
            "url": "https://docs.example.com/api",
            "mode": "fast",
            "output_profile": "rag_chunkable",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["structured_blocks"] is not None

    monkeypatch.setattr(
        "markdown_ingress.api_server.ingest_many",
        lambda **kwargs: type(
            "BatchResultStub",
            (),
            {
                "documents": [
                    ingest(
                        "https://docs.example.com/a",
                        mode="fast",
                        output_profile="rag_chunkable",
                        extract_blocks=True,
                        chunking_strategy="heading",
                    )
                ],
                "successful": 1,
                "failed": 0,
                "errors": {},
            },
        )(),
    )

    class StubJob:
        def __init__(self):
            self.job_id = "job-123"
            self.created_at = "2026-03-01T00:00:00+00:00"
            self.started_at = None
            self.completed_at = None
            self.status = "queued"
            self.result = None
            self.error = None
            self.ttl_seconds = 3600

    stub_job = StubJob()

    class StubQueue:
        ttl_seconds = 3600

        def submit(self, task, webhook_url=None, start_immediately=False):
            return stub_job

    monkeypatch.setattr(api_server, "_get_job_queue", lambda: StubQueue())
    monkeypatch.setattr(api_server, "_get_job_record", lambda job_id: stub_job if job_id == stub_job.job_id else None)

    accepted = client.post(
        "/api/v1/jobs/batch",
        json={"urls": ["https://docs.example.com/a"], "mode": "fast"},
    )
    assert accepted.status_code == 200
    job_id = accepted.json()["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] in {"queued", "running", "completed"}
