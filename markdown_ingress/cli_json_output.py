"""JSON serialization helpers for CLI outputs."""

from __future__ import annotations


def document_json_fields(doc) -> dict:
    """Serialize non-CLI-specific document fields for JSON outputs."""
    return {key: value for key, value in doc.to_serializable_dict().items() if key != "markdown"}


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
