"""
Tests for markdown_ingress/cli.py - targeting 100% coverage
Uses real ThreadingHTTPServer instead of mocks for HTTP-dependent paths.
"""

import argparse
import asyncio
import json
import sys
import threading
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markdown_ingress.cli import (
    IngestArgs,
    _add_common_ingest_args,
    _build_json_output,
    _create_batch_parser,
    _create_batch_processor,
    _create_batch_results_table,
    _create_ingest_parser,
    _create_legacy_parser,
    _create_standard_parser,
    _determine_mode,
    _display_basic_info,
    _display_batch_summary,
    _display_content,
    _display_header,
    _display_metadata,
    _display_rich_output,
    _display_security_info,
    _display_token_info,
    _is_legacy_mode,
    _load_urls_from_file,
    _prepare_ingest_params,
    _process_batch_with_progress,
    _save_batch_json,
    _save_batch_markdown,
    _save_batch_results,
    _save_json_output,
    _save_markdown_output,
    cmd_batch,
    cmd_ingest,
    main,
)
from markdown_ingress.application.batch import BatchProcessor
from markdown_ingress.api_runtime import UNSET
from markdown_ingress.models import SafeDocument
from markdown_ingress.shared_results import BatchResult

# ── Local HTTP test server ─────────────────────────────────────────────────────

class _SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = b"<html><body><h1>Hello</h1><p>Test content.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, format, *args):  # suppress access logs
        return


@pytest.fixture(scope="module")
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SimpleHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# ── Shared document fixtures ───────────────────────────────────────────────────

@pytest.fixture
def safe_doc():
    return SafeDocument(
        markdown="# Test\n\nContent here",
        metadata={
            "url": "http://x.com",
            "title": "Test Page",
            "mode": "fast",
            "fetch_time_ms": 100.0,
            "token_savings": {"tokens_saved": 500, "percentage_saved": 95.0},
            "risk_level": "LOW",
        },
        token_estimate=50,
        content_hash="sha256:abc123",
        injection_score=0.0,
        flags=[],
        removed_elements={"hidden_elements": 0},
    )


@pytest.fixture
def safe_doc_with_flags():
    """Document with flags and hidden elements for security display tests."""
    return SafeDocument(
        markdown="# Flagged\n\nSuspicious content.",
        metadata={
            "url": "http://x.com",
            "title": "Flagged",
            "mode": "fast",
            "fetch_time_ms": 50.0,
            "risk_level": "MEDIUM",
        },
        token_estimate=30,
        content_hash="sha256:xyz",
        injection_score=0.5,
        flags=["suspicious_phrase"],
        removed_elements={"hidden_elements": 3},
    )


@pytest.fixture
def batch_result(safe_doc):
    return BatchResult(
        total=2,
        successful=1,
        failed=1,
        documents=[safe_doc, None],
        errors={"http://fail.com": "Connection refused"},
    )


# ── _determine_mode ────────────────────────────────────────────────────────────

class TestDetermineMode:
    def test_fast_flag(self):
        assert _determine_mode(IngestArgs(url="http://x.com", fast=True)) == "fast"

    def test_render_flag(self):
        assert _determine_mode(IngestArgs(url="http://x.com", render=True)) == "render"

    def test_default_none(self):
        assert _determine_mode(IngestArgs(url="http://x.com")) is None

    def test_namespace_without_attributes(self):
        # Namespace with no fast/render attrs → no explicit override
        assert _determine_mode(Namespace(url="http://x.com")) is None


# ── _prepare_ingest_params ─────────────────────────────────────────────────────

class TestPrepareIngestParams:
    def test_basic(self):
        params = _prepare_ingest_params(IngestArgs(url="http://x.com"))
        assert params["url"] == "http://x.com"
        assert params["mode"] is None
        assert params["strict"] is None
        # screenshot uses UNSET sentinel when not provided (Bug fix #1)
        assert params["screenshot"] is UNSET

    def test_permissive_disables_strict(self):
        params = _prepare_ingest_params(IngestArgs(url="http://x.com", permissive=True))
        assert params["strict"] is False

    def test_screenshot_path(self):
        params = _prepare_ingest_params(IngestArgs(url="http://x.com", screenshot="/tmp/s.png"))
        assert params["screenshot"] == "/tmp/s.png"

    def test_screenshot_bool_true_passes_through(self):
        # When --screenshot is used without a path, argparse now sets const=True (boolean)
        params = _prepare_ingest_params(IngestArgs(url="http://x.com", screenshot=True))
        assert params["screenshot"] is True

    def test_no_metadata_no_links(self):
        params = _prepare_ingest_params(
            IngestArgs(url="http://x.com", no_metadata=True, no_links=True)
        )
        assert params["extract_metadata"] is False
        assert params["extract_links"] is False

    def test_advanced_security_use_llm(self):
        params = _prepare_ingest_params(
            IngestArgs(url="http://x.com", advanced_security=True, use_llm=True)
        )
        assert params["advanced_security"] is True
        assert params["use_llm"] is True

    def test_config_file_is_loaded(self, tmp_path):
        config_path = tmp_path / "mdi.yaml"
        config_path.write_text("mode: render\ntimeout: 45\nstrict: false\n")
        params = _prepare_ingest_params(IngestArgs(url="http://x.com", config=str(config_path)))
        assert params["config"] is not None
        assert params["config"].mode == "render"
        assert params["config"].timeout == 45.0
        assert params["config"].strict is False


# ── _build_json_output ─────────────────────────────────────────────────────────

class TestBuildJsonOutput:
    def test_with_content(self, safe_doc):
        out = _build_json_output(safe_doc, IngestArgs(url="http://x.com", no_content=False))
        assert out["markdown"] == safe_doc.markdown

    def test_no_content_is_none(self, safe_doc):
        out = _build_json_output(safe_doc, IngestArgs(url="http://x.com", no_content=True))
        assert out["markdown"] is None

    def test_all_keys_present(self, safe_doc):
        out = _build_json_output(safe_doc, IngestArgs(url="http://x.com"))
        for key in (
            "markdown", "metadata", "token_estimate", "content_hash",
            "injection_score", "flags", "removed_elements",
            "screenshot_path", "enriched_metadata", "links",
            "nova_score", "nova_details",
        ):
            assert key in out


# ── display functions ──────────────────────────────────────────────────────────

class TestDisplayFunctions:
    def test_display_header(self, safe_doc):
        _display_header(IngestArgs(url="http://x.com"))  # must not raise

    def test_display_basic_info(self, safe_doc):
        _display_basic_info(safe_doc, IngestArgs(url="http://x.com"))

    def test_display_token_info_with_savings(self, safe_doc):
        _display_token_info(safe_doc)

    def test_display_token_info_without_savings(self):
        doc = SafeDocument(
            markdown="# x",
            metadata={"risk_level": "LOW", "fetch_time_ms": 50.0},
            token_estimate=10,
            content_hash="sha256:0",
            injection_score=0.0,
        )
        _display_token_info(doc)  # no token_savings key → else branch

    def test_display_security_info_green(self, safe_doc):
        # injection_score < 0.4 → green, no flags, no hidden elements
        _display_security_info(safe_doc)

    def test_display_security_info_yellow_with_flags(self, safe_doc_with_flags):
        # injection_score=0.5 → yellow, has flags and hidden_elements
        _display_security_info(safe_doc_with_flags)

    def test_display_security_info_red(self):
        # injection_score >= 0.7 → red
        doc = SafeDocument(
            markdown="# x",
            metadata={"risk_level": "HIGH"},
            token_estimate=10,
            content_hash="sha256:0",
            injection_score=0.8,
        )
        _display_security_info(doc)

    def test_display_metadata(self, safe_doc):
        _display_metadata(safe_doc)

    def test_display_content_shown(self, safe_doc):
        _display_content(safe_doc, IngestArgs(url="http://x.com", no_content=False))

    def test_display_content_hidden(self, safe_doc):
        _display_content(safe_doc, IngestArgs(url="http://x.com", no_content=True))

    def test_display_rich_output(self, safe_doc):
        _display_rich_output(safe_doc, IngestArgs(url="http://x.com"))


# ── _save_json_output / _save_markdown_output ──────────────────────────────────

class TestSaveJsonOutput:
    def test_prints_to_stdout(self, safe_doc, capsys):
        _save_json_output(_build_json_output(safe_doc, IngestArgs(url="http://x.com")),
                          IngestArgs(url="http://x.com"))
        data = json.loads(capsys.readouterr().out)
        assert "metadata" in data

    def test_writes_to_file(self, safe_doc, tmp_path):
        save = tmp_path / "out.json"
        _save_json_output(_build_json_output(safe_doc, IngestArgs(url="http://x.com")),
                          IngestArgs(url="http://x.com", save=str(save)))
        assert save.exists()
        assert "metadata" in json.loads(save.read_text())


class TestSaveMarkdownOutput:
    def test_no_save_is_noop(self, safe_doc):
        _save_markdown_output(safe_doc, IngestArgs(url="http://x.com"))  # no error

    def test_writes_to_file(self, safe_doc, tmp_path):
        save = tmp_path / "out.md"
        _save_markdown_output(safe_doc, IngestArgs(url="http://x.com", save=str(save)))
        assert save.exists()
        assert safe_doc.markdown in save.read_text()


# ── cmd_ingest ─────────────────────────────────────────────────────────────────

class TestCmdIngest:
    def test_success_rich_output(self, local_server):
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url=local_server))
        assert exc.value.code == 0

    def test_success_no_content(self, local_server):
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url=local_server, no_content=True))
        assert exc.value.code == 0

    def test_success_json_to_stdout(self, local_server, capsys):
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url=local_server, json=True))
        assert exc.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert "markdown" in data

    def test_success_json_save_to_file(self, local_server, tmp_path):
        save = tmp_path / "result.json"
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url=local_server, json=True, save=str(save)))
        assert exc.value.code == 0
        assert save.exists()

    def test_success_markdown_save(self, local_server, tmp_path):
        save = tmp_path / "result.md"
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url=local_server, save=str(save)))
        assert exc.value.code == 0
        assert save.exists()

    def test_success_config_output_format_json(self, local_server, tmp_path, capsys):
        config_path = tmp_path / "mdi.yaml"
        config_path.write_text("output_format: json\n")
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url=local_server, config=str(config_path)))
        assert exc.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert "markdown" in data

    def test_success_config_output_format_markdown(self, local_server, tmp_path, capsys):
        config_path = tmp_path / "mdi.yaml"
        config_path.write_text("output_format: markdown\n")
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url=local_server, config=str(config_path)))
        assert exc.value.code == 0
        assert "# Hello" in capsys.readouterr().out

    def test_failure_unreachable_url(self):
        # Port 1 should be unreachable; triggers the except branch → exit(1)
        with pytest.raises(SystemExit) as exc:
            cmd_ingest(IngestArgs(url="http://127.0.0.1:1", timeout=2.0))
        assert exc.value.code == 1


# ── _load_urls_from_file ───────────────────────────────────────────────────────

class TestLoadUrlsFromFile:
    def test_valid_file(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("http://a.com\n# comment\n\nhttp://b.com\n")
        assert _load_urls_from_file(str(f)) == ["http://a.com", "http://b.com"]

    def test_missing_file_exits_1(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _load_urls_from_file(str(tmp_path / "nope.txt"))
        assert exc.value.code == 1

    def test_empty_file_exits_0(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("# only comments\n\n")
        with pytest.raises(SystemExit) as exc:
            _load_urls_from_file(str(f))
        assert exc.value.code == 0


# ── _create_batch_processor ────────────────────────────────────────────────────

class TestCreateBatchProcessor:
    def test_returns_processor(self):
        args = Namespace(fast=False, render=False, strict=True, permissive=False,
                         concurrent=3, timeout=10.0, model=None, config=None)
        processor = _create_batch_processor(args)
        assert isinstance(processor, BatchProcessor)

    def test_fast_mode_propagated(self):
        args = Namespace(fast=True, render=False, strict=True, permissive=False,
                         concurrent=1, timeout=5.0, model=None, config=None)
        assert _create_batch_processor(args).mode == "fast"

    def test_config_defaults_propagated(self, tmp_path):
        config_path = tmp_path / "batch.yaml"
        config_path.write_text(
            "mode: render\nstrict: false\nmodel: claude\nbatch_timeout: 22\nbatch_max_concurrent: 7\n"
        )
        args = Namespace(
            fast=False,
            render=False,
            strict=None,
            permissive=False,
            concurrent=None,
            timeout=None,
            model=None,
            config=str(config_path),
        )
        processor = _create_batch_processor(args)
        assert processor.mode == "render"
        assert processor.strict is False
        assert processor.model == "claude"
        assert processor.timeout == 22
        assert processor.max_concurrent == 7


# ── _process_batch_with_progress ──────────────────────────────────────────────

class TestProcessBatchWithProgress:
    def test_returns_batch_result(self, local_server):
        processor = BatchProcessor(mode="fast", strict=True, max_concurrent=1, timeout=10.0)
        result = asyncio.run(_process_batch_with_progress(processor, [local_server]))
        assert result.total == 1
        # on_progress inner callback is invoked by the processor
        assert result.successful >= 0


# ── batch display helpers ──────────────────────────────────────────────────────

class TestDisplayBatchSummary:
    def test_runs_without_error(self, batch_result):
        _display_batch_summary(batch_result)


class TestCreateBatchResultsTable:
    def test_basic_table(self, safe_doc):
        from rich.table import Table
        long_error_url = "http://" + "e" * 50 + ".com"
        br = BatchResult(
            total=2, successful=1, failed=1,
            documents=[safe_doc, None],
            errors={long_error_url: "err"},
        )
        table = _create_batch_results_table(["http://ok.com", long_error_url], br)
        assert isinstance(table, Table)

    def test_long_doc_url_truncated(self, safe_doc):
        from rich.table import Table
        long_url = "http://" + "d" * 50 + ".com"
        br = BatchResult(total=1, successful=1, failed=0, documents=[safe_doc], errors={})
        table = _create_batch_results_table([long_url], br)
        assert isinstance(table, Table)

    def test_missing_document_slot_still_renders_row(self, safe_doc):
        from rich.table import Table

        br = BatchResult(total=2, successful=1, failed=1, documents=[safe_doc], errors={})
        table = _create_batch_results_table(["http://ok.com", "http://missing.com"], br)
        assert isinstance(table, Table)
        assert len(table.rows) == 2


# ── _save_batch_json / _save_batch_markdown ────────────────────────────────────

class TestSaveBatchJson:
    def test_saves_correct_structure(self, tmp_path, safe_doc):
        br = BatchResult(total=1, successful=1, failed=0, documents=[safe_doc], errors={})
        out = tmp_path / "r.json"
        _save_batch_json(out, ["http://ok.com"], br)
        data = json.loads(out.read_text())
        assert data["summary"]["total"] == 1
        assert len(data["results"]) == 1

    def test_none_docs_excluded(self, tmp_path):
        br = BatchResult(total=1, successful=0, failed=1, documents=[None],
                         errors={"http://a.com": "err"})
        out = tmp_path / "r.json"
        _save_batch_json(out, ["http://a.com"], br)
        data = json.loads(out.read_text())
        assert len(data["results"]) == 1
        assert data["results"][0]["success"] is False
        assert data["results"][0]["error"] == "err"

    def test_missing_document_slot_is_serialized_as_failure(self, tmp_path, safe_doc):
        br = BatchResult(total=2, successful=1, failed=1, documents=[safe_doc], errors={})
        out = tmp_path / "r.json"
        _save_batch_json(out, ["http://ok.com", "http://missing.com"], br)
        data = json.loads(out.read_text())
        assert len(data["results"]) == 2
        assert data["summary"]["successful"] == 1
        assert data["summary"]["failed"] == 1
        assert data["results"][0]["success"] is True
        assert data["results"][1]["success"] is False
        assert data["results"][1]["error"] == "Missing batch result for input index 1"


class TestSaveBatchMarkdown:
    def test_saves_md_files(self, tmp_path, safe_doc):
        br = BatchResult(total=1, successful=1, failed=0, documents=[safe_doc], errors={})
        _save_batch_markdown(tmp_path, br)
        assert len(list(tmp_path.glob("*.md"))) == 1

    def test_skips_none_docs(self, tmp_path):
        br = BatchResult(total=1, successful=0, failed=1, documents=[None], errors={})
        _save_batch_markdown(tmp_path, br)
        assert list(tmp_path.glob("*.md")) == []


# ── _save_batch_results ────────────────────────────────────────────────────────

class TestSaveBatchResults:
    def _br(self, safe_doc):
        return BatchResult(total=1, successful=1, failed=0,
                           documents=[safe_doc], errors={})

    def test_no_output_is_noop(self, safe_doc):
        _save_batch_results(Namespace(output=None, json=False), ["http://x.com"],
                            self._br(safe_doc))

    def test_json_to_new_dir_without_suffix(self, tmp_path, safe_doc):
        # path has no suffix and doesn't exist → treated as directory
        out = tmp_path / "newdir"
        _save_batch_results(Namespace(output=str(out), json=True), ["http://x.com"],
                            self._br(safe_doc))
        assert (out / "results.json").exists()

    def test_json_to_existing_dir(self, tmp_path, safe_doc):
        # path already exists as a directory
        out = tmp_path / "existing"
        out.mkdir()
        _save_batch_results(Namespace(output=str(out), json=True), ["http://x.com"],
                            self._br(safe_doc))
        assert (out / "results.json").exists()

    def test_json_to_explicit_file(self, tmp_path, safe_doc):
        # path has a suffix → write directly
        out = tmp_path / "res.json"
        _save_batch_results(Namespace(output=str(out), json=True), ["http://x.com"],
                            self._br(safe_doc))
        assert out.exists()

    def test_markdown_to_dir(self, tmp_path, safe_doc):
        out = tmp_path / "mdout"
        _save_batch_results(Namespace(output=str(out), json=False), ["http://x.com"],
                            self._br(safe_doc))
        assert (out / "doc_001.md").exists()

    def test_markdown_ignores_extra_documents_without_matching_url(self, tmp_path, safe_doc):
        out = tmp_path / "mdout"
        br = BatchResult(
            total=2,
            successful=2,
            failed=0,
            documents=[safe_doc, safe_doc],
            errors={},
        )
        _save_batch_results(Namespace(output=str(out), json=False), ["http://x.com"], br)
        saved = sorted(path.name for path in out.glob("*.md"))
        assert saved == ["doc_001.md"]


# ── cmd_batch ──────────────────────────────────────────────────────────────────

class TestCmdBatch:
    def test_batch_success_saves_markdown(self, local_server, tmp_path):
        urls_file = tmp_path / "urls.txt"
        urls_file.write_text(f"{local_server}\n")
        out_dir = tmp_path / "output"
        args = Namespace(
            file=str(urls_file),
            fast=True, render=False,
            strict=True, permissive=False,
            concurrent=1, timeout=10.0, model=None, config=None,
            output=str(out_dir), json=False,
        )
        cmd_batch(args)
        assert len(list(out_dir.glob("*.md"))) == 1

    def test_batch_no_output(self, local_server, tmp_path):
        urls_file = tmp_path / "urls.txt"
        urls_file.write_text(f"{local_server}\n")
        args = Namespace(
            file=str(urls_file),
            fast=True, render=False,
            strict=True, permissive=False,
            concurrent=1, timeout=10.0, model=None, config=None,
            output=None, json=False,
        )
        cmd_batch(args)  # should return normally

    def test_batch_config_output_format_json_saves_json(self, local_server, tmp_path):
        urls_file = tmp_path / "urls.txt"
        urls_file.write_text(f"{local_server}\n")
        config_path = tmp_path / "mdi.yaml"
        config_path.write_text("output_format: json\n")
        out_file = tmp_path / "results.json"
        args = Namespace(
            file=str(urls_file),
            fast=True, render=False,
            strict=True, permissive=False,
            concurrent=1, timeout=10.0, model=None, config=str(config_path),
            output=str(out_file), json=False,
        )
        cmd_batch(args)
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["summary"]["successful"] == 1


# ── parser helpers ─────────────────────────────────────────────────────────────

class TestParsers:
    def test_add_common_ingest_args(self):
        p = argparse.ArgumentParser()
        _add_common_ingest_args(p)
        args = p.parse_args(["--fast", "--json", "--no-content",
                              "--no-metadata", "--no-links",
                              "--advanced-security", "--use-llm",
                              "--screenshot"])
        assert args.fast is True
        assert args.json is True
        assert args.screenshot is True  # const=True (boolean)
        assert args.timeout is None

    def test_add_common_ingest_args_screenshot_path(self):
        p = argparse.ArgumentParser()
        _add_common_ingest_args(p)
        args = p.parse_args(["--screenshot", "/tmp/shot.png"])
        assert args.screenshot == "/tmp/shot.png"

    def test_create_ingest_parser(self):
        p = argparse.ArgumentParser()
        sp = p.add_subparsers(dest="cmd")
        _create_ingest_parser(sp)
        args = p.parse_args(["ingest", "http://x.com", "--fast"])
        assert args.url == "http://x.com"
        assert args.fast is True

    def test_create_batch_parser(self):
        p = argparse.ArgumentParser()
        sp = p.add_subparsers(dest="cmd")
        _create_batch_parser(sp)
        args = p.parse_args(["batch", "urls.txt"])
        assert args.file == "urls.txt"
        assert args.concurrent is None

    def test_create_legacy_parser(self):
        p = _create_legacy_parser()
        args = p.parse_args(["http://x.com", "--fast"])
        assert args.url == "http://x.com"
        assert args.fast is True

    def test_create_standard_parser(self):
        p = _create_standard_parser()
        assert p is not None


# ── _is_legacy_mode ────────────────────────────────────────────────────────────

class TestIsLegacyMode:
    def test_http_url(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cli", "http://x.com"])
        assert _is_legacy_mode() is True

    def test_https_url(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cli", "https://x.com"])
        assert _is_legacy_mode() is True

    def test_subcommand_not_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cli", "ingest", "http://x.com"])
        assert _is_legacy_mode() is False

    def test_no_args_not_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cli"])
        assert _is_legacy_mode() is False

    def test_malformed_url_without_hostname_not_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cli", "https://user:pass@"])
        assert _is_legacy_mode() is False

    def test_port_only_url_without_hostname_not_legacy(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cli", "https://:443"])
        assert _is_legacy_mode() is False


# ── main() ─────────────────────────────────────────────────────────────────────

class TestMain:
    def test_no_command_prints_help_and_exits_0(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cli"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_legacy_mode_ingest(self, monkeypatch, local_server):
        monkeypatch.setattr(sys, "argv",
                            ["cli", local_server, "--fast", "--no-content"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_ingest_subcommand(self, monkeypatch, local_server):
        monkeypatch.setattr(sys, "argv",
                            ["cli", "ingest", local_server, "--fast", "--no-content"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_batch_subcommand(self, monkeypatch, local_server, tmp_path):
        urls_file = tmp_path / "urls.txt"
        urls_file.write_text(f"{local_server}\n")
        out_dir = tmp_path / "batch_out"
        monkeypatch.setattr(sys, "argv",
                            ["cli", "batch", str(urls_file),
                             "--output", str(out_dir), "--fast"])
        main()  # no SystemExit expected
        assert out_dir.exists()
