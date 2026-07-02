"""Output format selection for assembled documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from markdown_ingress.config_models import IngestConfig


@dataclass(frozen=True)
class DocumentOutputSelection:
    output_formats: list[str]
    security_explanation_payload: dict | None
    emitted_output_formats: list[str]


def build_output_selection(
    config: IngestConfig,
    structured_blocks: list,
    chunks: list,
    enriched_metadata: Any,
    security_result: dict,
) -> DocumentOutputSelection:
    output_formats = list(config.output_formats)
    if chunks and "chunks" not in output_formats:
        output_formats.append("chunks")

    available_formats = {"markdown"}
    if structured_blocks:
        available_formats.add("blocks")
    if chunks:
        available_formats.add("chunks")
    if enriched_metadata is not None:
        available_formats.add("metadata")

    security_explanation_payload = (
        cast(dict | None, security_result.get("explanation"))
        if config.include_security_explanation
        else None
    )
    if security_explanation_payload is not None:
        available_formats.add("security")

    emitted_output_formats: list[str] = []
    for fmt in [*output_formats, "markdown", "blocks", "chunks", "metadata", "security"]:
        if fmt in available_formats and fmt not in emitted_output_formats:
            emitted_output_formats.append(fmt)

    return DocumentOutputSelection(
        output_formats=output_formats,
        security_explanation_payload=security_explanation_payload,
        emitted_output_formats=emitted_output_formats,
    )
