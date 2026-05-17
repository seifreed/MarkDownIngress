"""Ingest and batch argument builders for the CLI."""

from __future__ import annotations

import argparse

# Constants aligned with config.py (must match Config validation)
MAX_CHUNK_SIZE_CLI = 50000
MAX_CHUNK_OVERLAP_CLI = 10000


def _validate_chunk_size(value):
    """Validate chunk size argument (shared between parsers)."""
    ivalue = int(value)
    if ivalue < 100 or ivalue > MAX_CHUNK_SIZE_CLI:
        raise argparse.ArgumentTypeError(f"chunk-size must be between 100 and {MAX_CHUNK_SIZE_CLI}")
    return ivalue


def _validate_chunk_overlap(value):
    """Validate chunk overlap argument (shared between parsers)."""
    ivalue = int(value)
    if ivalue < 0 or ivalue > MAX_CHUNK_OVERLAP_CLI:
        raise argparse.ArgumentTypeError(
            f"chunk-overlap must be between 0 and {MAX_CHUNK_OVERLAP_CLI}"
        )
    return ivalue


def _add_mode_args(parser, *, render_help: str, fast_help: str) -> None:
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--render", action="store_true", help=render_help)
    mode_group.add_argument("--fast", action="store_true", help=fast_help)


def _add_strictness_args(parser) -> None:
    strict_group = parser.add_mutually_exclusive_group()
    strict_group.add_argument("--strict", action="store_true", help="Enable strict security mode")
    strict_group.add_argument("--permissive", action="store_true", help="Disable strict mode")


def _add_screenshot_arg(parser) -> None:
    parser.add_argument(
        "--screenshot",
        nargs="?",
        const=True,
        help="Capture screenshot (render mode only). Optionally specify path.",
    )


def _add_metadata_args(parser) -> None:
    metadata_group = parser.add_mutually_exclusive_group()
    metadata_group.add_argument(
        "--metadata", action="store_true", help="Enable metadata extraction"
    )
    metadata_group.add_argument(
        "--no-metadata", action="store_true", help="Disable metadata extraction"
    )


def _add_link_args(parser) -> None:
    links_group = parser.add_mutually_exclusive_group()
    links_group.add_argument("--links", action="store_true", help="Enable link extraction")
    links_group.add_argument("--no-links", action="store_true", help="Disable link extraction")


def _add_advanced_security_args(parser) -> None:
    advanced_sec_group = parser.add_mutually_exclusive_group()
    advanced_sec_group.add_argument(
        "--advanced-security",
        action="store_true",
        help="Enable Nova-tracer advanced injection detection (requires nova-hunting)",
    )
    advanced_sec_group.add_argument(
        "--no-advanced-security",
        action="store_true",
        help="Disable Nova-tracer advanced injection detection",
    )


def _add_llm_args(parser) -> None:
    llm_group = parser.add_mutually_exclusive_group()
    llm_group.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable LLM-based detection tier (slow but most accurate, requires ANTHROPIC_API_KEY)",
    )
    llm_group.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM-based detection tier",
    )


def _add_output_structure_args(parser) -> None:
    parser.add_argument("--output-profile", default=None, help="Preset output profile")
    parser.add_argument(
        "--extract-blocks",
        action=argparse.BooleanOptionalAction,
        help="Emit structured blocks",
    )
    parser.add_argument(
        "--chunking-strategy",
        choices=["none", "heading", "size"],
        default=None,
        help="Enable native chunking strategy",
    )
    parser.add_argument(
        "--chunk-size",
        type=_validate_chunk_size,
        default=None,
        help=f"Target chunk size in characters (100-{MAX_CHUNK_SIZE_CLI})",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=_validate_chunk_overlap,
        default=None,
        help=f"Chunk overlap in characters (0-{MAX_CHUNK_OVERLAP_CLI})",
    )
    parser.add_argument("--render-cost-budget", type=int, default=None, help="Render cost budget")
    parser.add_argument("--domain-policy-file", help="JSON file with one or more domain policies")
    parser.add_argument(
        "--domain-policy",
        action="append",
        default=[],
        help="Inline JSON domain policy; may be repeated",
    )
    parser.add_argument("--show-blocks", action="store_true", help="Show structured block summary")
    parser.add_argument("--show-chunks", action="store_true", help="Show chunk summary")
    parser.add_argument(
        "--show-observability", action="store_true", help="Show observability data in rich output"
    )


def _add_ingest_policy_args(parser) -> None:
    _add_metadata_args(parser)
    _add_link_args(parser)
    _add_advanced_security_args(parser)
    _add_llm_args(parser)
    _add_output_structure_args(parser)


def add_common_ingest_args(parser):
    """Add common ingest arguments to a parser."""
    parser.add_argument("--config", help="Load runtime settings from a YAML/JSON config file")
    _add_mode_args(
        parser,
        render_help="Force render mode (Playwright)",
        fast_help="Force fast mode (HTTP only)",
    )
    _add_strictness_args(parser)
    parser.add_argument("--model", default=None, help="LLM model for token estimation")
    parser.add_argument("--timeout", type=float, default=None, help="Request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--no-content", action="store_true", help="Hide markdown content in output")
    parser.add_argument("--save", metavar="FILE", help="Save output to file")
    _add_screenshot_arg(parser)
    _add_ingest_policy_args(parser)


def create_ingest_parser(subparsers):
    ingest_parser = subparsers.add_parser("ingest", help="Ingest single URL")
    ingest_parser.add_argument("url", help="URL to ingest")
    add_common_ingest_args(ingest_parser)


def create_batch_parser(subparsers):
    batch_parser = subparsers.add_parser("batch", help="Process multiple URLs from file")
    batch_parser.add_argument("file", help="File containing URLs (one per line)")
    batch_parser.add_argument("--config", help="Load runtime settings from a YAML/JSON config file")
    _add_mode_args(
        batch_parser,
        render_help="Force render mode for all URLs",
        fast_help="Force fast mode for all URLs",
    )
    _add_strictness_args(batch_parser)
    batch_parser.add_argument("--model", default=None, help="LLM model for token estimation")
    batch_parser.add_argument("--timeout", type=float, default=None, help="Request timeout per URL")
    batch_parser.add_argument(
        "--concurrent", type=int, default=None, help="Max concurrent requests"
    )
    batch_parser.add_argument("--json", action="store_true", help="Save results as JSON")
    batch_parser.add_argument(
        "--no-content", action="store_true", help="Hide markdown content in output"
    )
    batch_parser.add_argument("--output", "-o", help="Output directory (markdown) or file (json)")
    _add_screenshot_arg(batch_parser)
    _add_ingest_policy_args(batch_parser)
