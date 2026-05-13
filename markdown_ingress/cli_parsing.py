"""Parser and argument preparation helpers for the CLI."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse

from markdown_ingress import __version__
from markdown_ingress.api_runtime import UNSET
from markdown_ingress.cli_support import load_domain_policies
from markdown_ingress.core.config import Config


@dataclass
class IngestArgs:
    """Arguments for ingest command (used in legacy mode)."""

    url: str
    config: str | None = None
    render: bool = False
    fast: bool = False
    strict: bool | None = None
    permissive: bool = False
    model: str | None = None
    timeout: float | None = None
    json: bool = False
    no_content: bool = False
    save: str | None = None
    screenshot: bool | str | None = None
    no_metadata: bool = False
    no_links: bool = False
    advanced_security: bool = False
    use_llm: bool = False


def determine_mode(args):
    """Determine ingestion mode from args."""
    if hasattr(args, "fast") and args.fast:
        return "fast"
    if hasattr(args, "render") and args.render:
        return "render"
    return None


def load_runtime_config(args) -> Config:
    """Load config from file, defaults, and environment variables.

    This function always returns a Config object, even when no explicit
    config file is provided. Environment variables like MDI_MODE, MDI_STRICT,
    etc. are always applied.

    Args:
        args: Parsed CLI arguments (may have .config attribute)

    Returns:
        Config object with defaults, file config (if any), and env vars applied
    """
    from .core.config import ConfigLoader

    config_path = getattr(args, "config", None)
    loader = ConfigLoader(config_path)
    return cast(Config, loader.load())


def prepare_ingest_params(args, runtime_config: Config | None = None):
    """Prepare parameters for ingest call."""
    if runtime_config is None:
        runtime_config = load_runtime_config(args)
    runtime_ingest_config = (
        runtime_config.to_ingest_config() if runtime_config is not None else None
    )
    # Handle strict flag: distinguish between explicit False, explicit True, and not set
    # --strict sets strict=True, --permissive sets strict=False
    # If neither is set, use runtime_config.strict or None (let config resolution handle default)
    strict = None
    if getattr(args, "permissive", False):
        strict = False
    elif getattr(args, "strict", False):
        strict = True

    mode = determine_mode(args)

    # Screenshot: use UNSET sentinel when not provided, otherwise pass the value
    # This distinguishes "not provided" from "explicitly set to None"
    screenshot = UNSET
    if hasattr(args, "screenshot") and args.screenshot:
        # args.screenshot is True (const) or a path string
        screenshot = args.screenshot

    # Boolean flags: support both positive and negative flags for symmetry
    # --metadata / --no-metadata: explicit enable/disable, None = use config default
    # --links / --no-links: explicit enable/disable, None = use config default
    # --advanced-security / --no-advanced-security: explicit enable/disable.
    # None = use config default.
    # --use-llm / --no-llm: explicit enable/disable, None = use config default
    extract_metadata = None
    if getattr(args, "metadata", False):
        extract_metadata = True
    elif getattr(args, "no_metadata", False):
        extract_metadata = False

    extract_links = None
    if getattr(args, "links", False):
        extract_links = True
    elif getattr(args, "no_links", False):
        extract_links = False

    advanced_security = None
    if getattr(args, "advanced_security", False):
        advanced_security = True
    elif getattr(args, "no_advanced_security", False):
        advanced_security = False

    use_llm = None
    if getattr(args, "use_llm", False):
        use_llm = True
    elif getattr(args, "no_llm", False):
        use_llm = False

    domain_policies = load_domain_policies(args)

    return {
        "url": args.url,
        "config": runtime_ingest_config,
        "mode": mode,
        "strict": strict,
        "model": args.model,
        "timeout": args.timeout,
        "screenshot": screenshot,
        "extract_metadata": extract_metadata,
        "extract_links": extract_links,
        "advanced_security": advanced_security,
        "use_llm": use_llm,
        "output_profile": getattr(args, "output_profile", None),
        # BooleanOptionalAction: True (enabled), False (disabled), None (use default)
        "extract_blocks": getattr(args, "extract_blocks", None),
        "chunking_strategy": getattr(args, "chunking_strategy", None),
        "chunk_size": getattr(args, "chunk_size", None),
        "chunk_overlap": getattr(args, "chunk_overlap", None),
        "render_cost_budget": getattr(args, "render_cost_budget", None),
        "domain_policies": domain_policies,
    }


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


def create_compare_parser(subparsers):
    compare_parser = subparsers.add_parser(
        "compare", help="Compare extractors on a local HTML file"
    )
    compare_parser.add_argument("file", help="HTML file to compare")
    compare_parser.add_argument("--model", default=None, help="LLM model for token estimation")
    compare_parser.add_argument("--json", action="store_true", help="Output as JSON")


def create_benchmark_parser(subparsers):
    benchmark_parser = subparsers.add_parser("benchmark", help="Benchmark a list of URLs")
    benchmark_parser.add_argument("file", help="File containing URLs (one per line)")
    benchmark_parser.add_argument("--model", default=None, help="LLM model for token estimation")
    benchmark_parser.add_argument("--iterations", type=int, default=3, help="Iterations per URL")
    benchmark_parser.add_argument("--output", "-o", help="Write report to file")
    benchmark_parser.add_argument(
        "--compare-extractors",
        action="store_true",
        help="Run extractor comparison when HTML is available",
    )
    benchmark_parser.add_argument("--render", action="store_true", help="Force render mode")
    benchmark_parser.add_argument("--fast", action="store_true", help="Force fast mode")


def _is_valid_url(url_string: str) -> bool:
    """Validate that a URL string is well-formed with a valid host.

    Args:
        url_string: URL string to validate

    Returns:
        True if URL has valid scheme and hostname, False otherwise.
    """
    try:
        result = urlparse(url_string)
        # Must have a scheme (http/https) and a network location (hostname)
        return result.scheme in ("http", "https") and bool(result.hostname)
    except Exception:
        return False


def is_legacy_mode():
    """Check if we're in legacy mode (direct URL without subcommand).

    Returns True if:
    - There is at least one command-line argument
    - The first argument starts with http:// or https://
    - The argument is a valid URL with a non-empty hostname

    This prevents malformed URLs like 'http://' (no host) from triggering legacy mode.
    """
    if len(sys.argv) <= 1:
        return False

    first_arg = sys.argv[1]

    # Quick check for http/https scheme before doing more expensive validation
    if not (first_arg.startswith("http://") or first_arg.startswith("https://")):
        return False

    # Validate that the URL is well-formed with a valid host
    return _is_valid_url(first_arg)


def create_legacy_parser():
    legacy_parser = argparse.ArgumentParser(
        description="MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine"
    )
    legacy_parser.add_argument("url", help="URL to ingest")
    add_common_ingest_args(legacy_parser)
    return legacy_parser


def create_standard_parser():
    parser = argparse.ArgumentParser(
        description="MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser
