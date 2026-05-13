"""Output profile defaults for runtime ingestion configuration."""

from __future__ import annotations

import copy
from typing import Any

from markdown_ingress.config_validation import VALID_OUTPUT_PROFILES

_OUTPUT_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "default": {},
    "llm_safe": {
        "strict": True,
        "extract_metadata": True,
        "extract_links": True,
        "extract_blocks": True,
        "chunking_strategy": "heading",
        "output_formats": ["markdown", "blocks", "security"],
    },
    "rag_chunkable": {
        "extract_metadata": True,
        "extract_links": True,
        "extract_blocks": True,
        "chunking_strategy": "heading",
        "chunk_size": 900,
        "chunk_overlap": 120,
        "output_formats": ["markdown", "blocks", "chunks"],
    },
    "for_search": {
        "mode": "fast",
        "strict": False,
        "extract_metadata": True,
        "extract_links": True,
        "extract_blocks": True,
        "chunking_strategy": "size",
        "chunk_size": 700,
        "chunk_overlap": 80,
        "output_formats": ["markdown", "blocks", "chunks", "metadata"],
    },
    "for_archive": {
        "mode": "render",
        "strict": True,
        "extract_metadata": True,
        "extract_links": True,
        "extract_blocks": True,
        "chunking_strategy": "none",
        "output_formats": ["markdown", "blocks", "metadata", "security"],
    },
}


def output_profile_fields() -> frozenset[str]:
    """Return the set of config fields managed by output profiles."""
    field_names: set[str] = set()
    for profile in VALID_OUTPUT_PROFILES:
        field_names.update(_OUTPUT_PROFILE_DEFAULTS.get(profile, {}).keys())
    return frozenset(field_names)


def output_profile_defaults(profile: str) -> dict[str, Any]:
    """Return runtime overrides for a named output profile."""
    return copy.deepcopy(_OUTPUT_PROFILE_DEFAULTS.get(profile, {}))


def is_known_output_profile(profile: str) -> bool:
    """Return whether the output profile name is recognized."""
    return profile in VALID_OUTPUT_PROFILES
