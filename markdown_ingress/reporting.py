"""Helpers for building and persisting security reports."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from markdown_ingress.models import SafeDocument, SecurityReport


def report_filename(report: SecurityReport) -> str:
    """Build a stable, human-readable JSON filename for an auto-saved report."""
    parsed_timestamp = datetime.fromisoformat(report.timestamp.replace("Z", "+00:00"))
    if parsed_timestamp.tzinfo is None:
        parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)
    else:
        parsed_timestamp = parsed_timestamp.astimezone(UTC)
    timestamp = parsed_timestamp.strftime("%Y%m%dT%H%M%SZ")
    hostname = (urlsplit(report.url).hostname or "report").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", hostname).strip("-") or "report"
    return f"{timestamp}-{slug}.json"


def persist_security_report(
    report: SecurityReport,
    reports_dir: str,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Persist a generated security report JSON to the configured reports directory.

    Args:
        base_dir: If provided, ``reports_dir`` is resolved relative to this base
            and must stay within it (path-traversal protection).  When *None* the
            current working directory is used as the base.
    """
    reports_path = Path(reports_dir)
    if base_dir is not None:
        # When a base_dir is given, reports_dir must stay within it
        effective_base = base_dir.resolve()
        directory = (effective_base / reports_dir).resolve()
        if not str(directory).startswith(str(effective_base)):
            raise ValueError(
                f"reports_dir escapes the allowed base directory: {reports_dir!r}"
            )
    else:
        # No explicit base — resolve the path as given but reject .. segments
        directory = reports_path.resolve()
    # Reject paths that contain traversal sequences in the raw input
    if ".." in reports_path.parts:
        raise ValueError(
            f"reports_dir must not contain '..' path segments: {reports_dir!r}"
        )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / report_filename(report)
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        counter = 2
        while True:
            candidate = directory / f"{stem}-{counter}{suffix}"
            if not candidate.exists():
                target = candidate
                break
            counter += 1
    report.save(str(target))
    return target


def security_report_from_document(doc: SafeDocument) -> SecurityReport:
    """Build a security report directly from an ingested document."""
    return SecurityReport(
        injection_score=doc.injection_score,
        risk_level=doc.metadata.get("risk_level", "UNKNOWN"),
        pattern_matches=doc.metadata.get("pattern_matches", []),
        flags=doc.flags,
        hidden_content_detected=doc.metadata.get(
            "hidden_content_detected",
            "hidden_content" in doc.flags,
        ),
        hidden_elements_count=doc.removed_elements.get("hidden_elements", 0),
        imperative_density=doc.metadata.get("imperative_density", 0.0),
        url=doc.metadata.get("url", ""),
        title=doc.metadata.get("title", ""),
        token_estimate=doc.token_estimate,
        token_reduction_percent=doc.metadata.get("token_savings", {}).get("savings_percent", 0.0),
        original_size_bytes=doc.metadata.get("original_size_bytes", 0),
        cleaned_size_bytes=doc.metadata.get(
            "cleaned_size_bytes", len(doc.markdown.encode("utf-8"))
        ),
        content_hash=doc.content_hash,
        structural_hash=doc.metadata.get("structural_hash", ""),
        removed_elements=doc.removed_elements,
        language=doc.metadata.get("language"),
        explanation=doc.security_explanation if doc.security_explanation is not None else {},
        observability=doc.observability if doc.observability is not None else {},
    )


def persist_report_for_document(doc: SafeDocument, reports_dir: str) -> Path:
    """Persist a report derived from a SafeDocument."""
    return persist_security_report(security_report_from_document(doc), reports_dir)
