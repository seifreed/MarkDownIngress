import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import markdown_ingress.api_server as api_server
from markdown_ingress import DomainPolicy, IngestConfig, compare_extractors, ingest
from markdown_ingress.api_server import app
from markdown_ingress.core.config import Config
from markdown_ingress.core.document_builder import build_policy_engine, process_fetched_content
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
        "markdown_ingress.adapters.fetching.httpx_fetcher.Fetcher.fetch_sync",
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
        "markdown_ingress.adapters.fetching.httpx_fetcher.Fetcher.fetch_sync",
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


def test_domain_policy_output_profile_overrides_explicit_base_profile_fields():
    config = IngestConfig(
        mode="fast",
        strict=False,
        domain_policies=[
            DomainPolicy(
                domain="example.com",
                output_profile="for_archive",
            )
        ],
    )

    resolved, matched = config.resolve_for_url("https://example.com/page")

    assert matched is not None
    assert resolved.output_profile == "for_archive"
    assert resolved.mode == "render"
    assert resolved.strict is True
    assert resolved.chunking_strategy == "none"
    assert resolved.output_formats == ["markdown", "blocks", "metadata", "security"]


def test_domain_policy_scalar_override_still_wins_over_domain_output_profile():
    config = IngestConfig(
        mode="fast",
        strict=False,
        domain_policies=[
            DomainPolicy(
                domain="example.com",
                output_profile="for_archive",
                mode="fast",
            )
        ],
    )

    resolved, matched = config.resolve_for_url("https://example.com/page")

    assert matched is not None
    assert resolved.output_profile == "for_archive"
    assert resolved.mode == "fast"
    assert resolved.strict is True


def test_build_policy_engine_preserves_warning_band_for_conflicting_thresholds():
    engine = build_policy_engine(
        "normal",
        DomainPolicy(
            domain="example.com",
            block_threshold=0.2,
            warn_threshold=0.4,
        ),
    )

    assert engine.policy.block_threshold == 0.2
    assert engine.policy.warn_threshold == 0.19
    assert engine.get_action(0.19) == "warn"
    assert engine.get_action(0.2) == "block"


def test_build_policy_engine_clamps_warning_to_zero_when_block_is_zero():
    engine = build_policy_engine(
        "normal",
        DomainPolicy(
            domain="example.com",
            block_threshold=0.0,
            warn_threshold=0.1,
        ),
    )

    assert engine.policy.block_threshold == 0.0
    assert engine.policy.warn_threshold == 0.0
    assert engine.get_action(0.0) == "block"


def test_resolve_for_url_does_not_create_phantom_dom_attributes():
    """Bug fix: DomainPolicy DOM fields must not be set as dynamic IngestConfig attributes."""
    config = IngestConfig(
        domain_policies=[
            DomainPolicy(
                domain="example.com",
                allowed_tags=["article", "p"],
                blocked_tags=["form"],
                blocked_selectors=[".ads"],
                unwrap_selectors=["div.wrapper"],
                mode="fast",
            )
        ],
    )
    resolved, matched = config.resolve_for_url("https://example.com/page")
    assert matched is not None
    assert resolved.mode == "fast"
    # These DomainPolicy-specific fields must NOT exist on IngestConfig
    assert not hasattr(resolved, "allowed_tags")
    assert not hasattr(resolved, "blocked_tags")
    assert not hasattr(resolved, "blocked_selectors")
    assert not hasattr(resolved, "unwrap_selectors")


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


def test_config_overrides_become_explicit():
    """Cloning a config and setting fields registers them in explicit_keys."""
    config = IngestConfig(output_profile="for_archive")
    merged = config.clone()
    merged.strict = False
    object.__setattr__(
        merged, "_explicit_keys", frozenset(config.explicit_keys() | {"strict"})
    )
    merged.validate()
    assert "strict" in merged.explicit_keys()
    resolved, _ = merged.resolve_for_url("https://example.com/page")
    assert resolved.strict is False


def test_metadata_tracks_requested_and_emitted_output_formats_separately():
    config = IngestConfig(
        mode="fast",
        extract_blocks=False,
        extract_metadata=False,
        include_security_explanation=False,
    )
    config.output_formats = ["markdown", "blocks"]
    document = process_fetched_content(
        SimpleNamespace(
            extractor=None,
            md_converter=None,
            hasher=None,
            token_estimator=None,
            scorer=None,
            metadata_extractor=None,
            link_analyzer=None,
        ),
        _make_fetch_result(
            "https://docs.example.com/page",
            "<html><body><article><h1>Doc</h1><p>Content</p></article></body></html>",
        ),
        config,
    )

    assert document.structured_blocks is None
    assert document.metadata["output_formats"] == ["markdown", "blocks"]
    assert document.metadata["emitted_output_formats"] == ["markdown"]


def test_markdown_preserves_code_fences_and_tables(monkeypatch):
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.Fetcher.fetch_sync",
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


def test_compare_extractors_handles_empty_html():
    result = compare_extractors("")

    assert "readability" in result
    assert result["readability"]["available"] is False
    assert result["readability"]["markdown"] == ""


def test_compare_extractors_handles_trafilatura_runtime_failure(monkeypatch):
    fake_module = SimpleNamespace(
        extract=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setitem(sys.modules, "trafilatura", fake_module)

    result = compare_extractors(FIXTURE_HTML)

    assert result["trafilatura"]["available"] is False
    assert result["trafilatura"]["markdown"] == ""
    assert result["trafilatura"]["notes"] == ["trafilatura failed: RuntimeError: boom"]


def test_versioned_api_and_batch_jobs(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.Fetcher.fetch_sync",
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
    monkeypatch.setattr(
        api_server,
        "_get_job_record",
        lambda job_id: stub_job if job_id == stub_job.job_id else None,
    )

    accepted = client.post(
        "/api/v1/jobs/batch",
        json={"urls": ["https://docs.example.com/a"], "mode": "fast"},
    )
    assert accepted.status_code == 200
    job_id = accepted.json()["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] in {"queued", "running", "completed"}
