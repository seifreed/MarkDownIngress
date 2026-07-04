"""Contract tests for SQLite queue SQL declarations."""

from __future__ import annotations

from pathlib import Path


def _read_module(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_job_queue_queue_mixins_use_shared_begin_immediate_sql_constant() -> None:
    modules = [
        "markdown_ingress/adapters/jobs/job_queue_cleanup.py",
        "markdown_ingress/adapters/jobs/job_queue_lifecycle.py",
        "markdown_ingress/adapters/jobs/sqlite_job_queue.py",
    ]
    for module in modules:
        text = _read_module(module)
        assert 'conn.execute("BEGIN IMMEDIATE")' not in text
        assert "conn.execute('BEGIN IMMEDIATE')" not in text
