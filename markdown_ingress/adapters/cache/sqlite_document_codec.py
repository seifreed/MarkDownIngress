"""SafeDocument JSON codec used by the SQLite cache backend."""

from __future__ import annotations

import dataclasses
import json

from markdown_ingress.models import SafeDocument


def serialize_document(doc: SafeDocument) -> str:
    """Serialize SafeDocument to JSON."""
    return json.dumps(
        {
            "markdown": doc.markdown,
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
    )


def deserialize_document(json_str: str) -> SafeDocument:
    """Deserialize JSON to SafeDocument with forward/backward compatibility."""
    data = json.loads(json_str)
    if not isinstance(data, dict):
        raise ValueError(
            f"Cache entry root has unexpected type {type(data).__name__}; expected object"
        )

    known_fields = {field.name for field in dataclasses.fields(SafeDocument)}
    filtered = {key: value for key, value in data.items() if key in known_fields}
    for field in dataclasses.fields(SafeDocument):
        if field.name in filtered:
            continue
        if field.default is not dataclasses.MISSING:
            filtered[field.name] = field.default
        elif field.default_factory is not dataclasses.MISSING:
            filtered[field.name] = field.default_factory()

    _validate_primitive_fields(filtered)
    return SafeDocument(**filtered)


def _validate_primitive_fields(filtered: dict[str, object]) -> None:
    primitive_field_types: dict[str, type | tuple[type, ...]] = {
        "markdown": str,
        "title": (str, type(None)),
        "url": str,
        "token_estimate": int,
        "injection_score": (int, float),
        "content_hash": (str, type(None)),
    }
    for field_name, expected in primitive_field_types.items():
        if field_name in filtered and not isinstance(filtered[field_name], expected):
            raise ValueError(
                f"Cache entry field '{field_name}' has unexpected type "
                f"{type(filtered[field_name]).__name__}"
            )
