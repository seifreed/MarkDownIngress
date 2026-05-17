"""Parser and argument preparation helpers for the CLI."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse

from markdown_ingress import __version__
from markdown_ingress.api_runtime import UNSET
from markdown_ingress.cli_ingest_args import MAX_CHUNK_OVERLAP_CLI as MAX_CHUNK_OVERLAP_CLI
from markdown_ingress.cli_ingest_args import MAX_CHUNK_SIZE_CLI as MAX_CHUNK_SIZE_CLI
from markdown_ingress.cli_ingest_args import (
    _add_advanced_security_args as _add_advanced_security_args,
)
from markdown_ingress.cli_ingest_args import _add_ingest_policy_args as _add_ingest_policy_args
from markdown_ingress.cli_ingest_args import _add_link_args as _add_link_args
from markdown_ingress.cli_ingest_args import _add_llm_args as _add_llm_args
from markdown_ingress.cli_ingest_args import _add_metadata_args as _add_metadata_args
from markdown_ingress.cli_ingest_args import _add_mode_args as _add_mode_args
from markdown_ingress.cli_ingest_args import (
    _add_output_structure_args as _add_output_structure_args,
)
from markdown_ingress.cli_ingest_args import _add_screenshot_arg as _add_screenshot_arg
from markdown_ingress.cli_ingest_args import _add_strictness_args as _add_strictness_args
from markdown_ingress.cli_ingest_args import _validate_chunk_overlap as _validate_chunk_overlap
from markdown_ingress.cli_ingest_args import _validate_chunk_size as _validate_chunk_size
from markdown_ingress.cli_ingest_args import add_common_ingest_args as add_common_ingest_args
from markdown_ingress.cli_ingest_args import create_batch_parser as create_batch_parser
from markdown_ingress.cli_ingest_args import create_ingest_parser as create_ingest_parser
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


def _runtime_ingest_config(args, runtime_config: Config | None):
    if runtime_config is None:
        runtime_config = load_runtime_config(args)
    return runtime_config.to_ingest_config() if runtime_config is not None else None


def _resolve_strict_arg(args):
    if getattr(args, "permissive", False):
        return False
    if getattr(args, "strict", False):
        return True
    return None


def _resolve_screenshot_arg(args):
    if hasattr(args, "screenshot") and args.screenshot:
        return args.screenshot
    return UNSET


def _resolve_optional_bool_arg(args, enabled_name: str, disabled_name: str):
    if getattr(args, enabled_name, False):
        return True
    if getattr(args, disabled_name, False):
        return False
    return None


def _extract_cli_feature_flags(args):
    return {
        "extract_metadata": _resolve_optional_bool_arg(args, "metadata", "no_metadata"),
        "extract_links": _resolve_optional_bool_arg(args, "links", "no_links"),
        "advanced_security": _resolve_optional_bool_arg(
            args, "advanced_security", "no_advanced_security"
        ),
        "use_llm": _resolve_optional_bool_arg(args, "use_llm", "no_llm"),
    }


def _extract_cli_structure_options(args):
    return {
        "output_profile": getattr(args, "output_profile", None),
        "extract_blocks": getattr(args, "extract_blocks", None),
        "chunking_strategy": getattr(args, "chunking_strategy", None),
        "chunk_size": getattr(args, "chunk_size", None),
        "chunk_overlap": getattr(args, "chunk_overlap", None),
        "render_cost_budget": getattr(args, "render_cost_budget", None),
    }


def prepare_ingest_params(args, runtime_config: Config | None = None):
    """Prepare parameters for ingest call."""
    runtime_ingest_config = _runtime_ingest_config(args, runtime_config)
    domain_policies = load_domain_policies(args)

    return {
        "url": args.url,
        "config": runtime_ingest_config,
        "mode": determine_mode(args),
        "strict": _resolve_strict_arg(args),
        "model": args.model,
        "timeout": args.timeout,
        "screenshot": _resolve_screenshot_arg(args),
        **_extract_cli_feature_flags(args),
        **_extract_cli_structure_options(args),
        "domain_policies": domain_policies,
    }


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
