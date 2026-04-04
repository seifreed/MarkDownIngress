#!/usr/bin/env python3
"""MarkDownIngress CLI."""

import sys

from markdown_ingress import __version__

from markdown_ingress.cli_commands import (
    build_json_output,
    cmd_batch,
    cmd_benchmark,
    cmd_compare,
    cmd_ingest,
    create_batch_processor,
    ingest_many_with_progress,
    process_batch_with_progress,
)
from markdown_ingress.cli_parsing import (
    IngestArgs,
    add_common_ingest_args,
    create_batch_parser,
    create_benchmark_parser,
    create_compare_parser,
    create_ingest_parser,
    create_legacy_parser,
    create_standard_parser,
    determine_mode,
    is_legacy_mode,
    prepare_ingest_params,
)
from markdown_ingress.cli_support import (
    create_batch_results_table,
    display_basic_info,
    display_batch_summary,
    display_content,
    display_header,
    display_metadata,
    display_rich_output,
    display_security_info,
    display_token_info,
    load_urls_from_file,
    save_batch_results,
    save_json_output,
    save_markdown_output,
)

_determine_mode = determine_mode
_prepare_ingest_params = prepare_ingest_params
_build_json_output = build_json_output
_create_batch_processor = create_batch_processor
_process_batch_with_progress = process_batch_with_progress
_ingest_many_with_progress = ingest_many_with_progress
_add_common_ingest_args = add_common_ingest_args
_create_ingest_parser = create_ingest_parser
_create_batch_parser = create_batch_parser
_create_compare_parser = create_compare_parser
_create_benchmark_parser = create_benchmark_parser
_is_legacy_mode = is_legacy_mode
_create_legacy_parser = create_legacy_parser
_create_standard_parser = create_standard_parser

def _display_header(_args=None):
    return display_header(__version__)


_display_basic_info = display_basic_info
_display_token_info = display_token_info
_display_security_info = display_security_info
_display_metadata = display_metadata
_display_content = display_content


def _display_rich_output(doc, args):
    return display_rich_output(doc, args, __version__)
_load_urls_from_file = load_urls_from_file
_display_batch_summary = display_batch_summary
_create_batch_results_table = create_batch_results_table
_save_json_output = save_json_output
_save_markdown_output = save_markdown_output
_save_batch_results = save_batch_results


def _save_batch_json(output_path, urls, batch_result):
    args = type("Args", (), {"output": str(output_path), "json": True})()
    save_batch_results(args, urls, batch_result)


def _save_batch_markdown(output_path, batch_result):
    args = type("Args", (), {"output": str(output_path), "json": False})()
    save_batch_results(args, [], batch_result)


def main():
    """Main CLI entry point."""
    if is_legacy_mode():
        parser = create_legacy_parser()
        args = parser.parse_args()
        cmd_ingest(args)
        return

    parser = create_standard_parser()
    subparsers = parser.add_subparsers(dest="command", help="Commands", required=False)
    create_ingest_parser(subparsers)
    create_batch_parser(subparsers)
    create_compare_parser(subparsers)
    create_benchmark_parser(subparsers)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    if args.command == "ingest":
        cmd_ingest(args)
    elif args.command == "batch":
        cmd_batch(args)
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)


if __name__ == "__main__":
    main()
