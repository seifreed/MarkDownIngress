"""JSON serialization helpers for CLI outputs."""

from __future__ import annotations


def document_json_fields(doc) -> dict:
    """Serialize non-CLI-specific document fields for JSON outputs."""
    return {
        "metadata": doc.metadata,
        "token_estimate": doc.token_estimate,
        "content_hash": doc.content_hash,
        "injection_score": doc.injection_score,
        "flags": doc.flags,
        "removed_elements": doc.removed_elements,
        "screenshot_path": doc.screenshot_path,
        "enriched_metadata": doc.enriched_metadata,
        "links": doc.links,
        "nova_score": doc.nova_score,
        "nova_details": doc.nova_details,
        "structured_blocks": doc.structured_blocks,
        "chunks": doc.chunks,
        "security_explanation": doc.security_explanation,
        "observability": doc.observability,
    }


def batch_document_json_row(row: dict, *, no_content: bool = False) -> dict:
    """Serialize one successful batch row for JSON outputs."""
    document = row["document"]
    return {
        "url": row["url"],
        "success": True,
        "markdown": None if no_content else document.markdown,
        **document_json_fields(document),
        "tokens": document.token_estimate,
        "error": row["error"],
    }
