"""Integration tests for architecture boundaries without mocks."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from markdown_ingress import compare_extractors
from markdown_ingress.adapters.jobs.sqlite_job_queue import PersistentJobQueue
from markdown_ingress.adapters.webhooks.http_notifier import HTTPWebhookNotifier
from markdown_ingress.application.batch import BatchProcessor


def _start_html_server(html: bytes) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_batch_processor_uses_application_layer_without_mocking():
    html = b"<html><body><article><h1>Batch</h1><p>Integration content.</p></article></body></html>"
    server, url = _start_html_server(html)
    try:
        processor = BatchProcessor(mode="fast", timeout=10.0, max_concurrent=1)
        result = processor.process_batch([url])
        assert result.successful == 1
        assert result.documents[0] is not None
        assert "Batch" in result.documents[0].markdown
    finally:
        server.shutdown()


def test_sqlite_job_queue_delivers_real_webhook(tmp_path: Path):
    received: list[dict[str, object]] = []

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            received.append(payload)
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webhook_url = f"http://127.0.0.1:{server.server_address[1]}/hook"

    try:
        queue = PersistentJobQueue(
            str(tmp_path / "jobs.sqlite3"),
            worker_count=1,
            ttl_seconds=3600,
            notifier=HTTPWebhookNotifier(max_retries=2, retry_delay_seconds=0.0),
            allow_local_webhooks=True,  # Allow localhost for testing
        )
        job = queue.submit(
            lambda: {"ok": True, "source": "integration"},
            webhook_url=webhook_url,
            start_immediately=True,
        )
        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.status == "completed"
        assert received
        assert received[0]["job_id"] == job.job_id
        assert received[0]["result"] == {"ok": True, "source": "integration"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_compare_extractors_use_case_runs_without_mocking():
    html = """
    <html><body><article><h1>Docs</h1><p>Paragraph.</p><pre><code>print('x')</code></pre></article></body></html>
    """
    results = compare_extractors(html)
    assert "readability" in results
    assert "available" in results["readability"]
