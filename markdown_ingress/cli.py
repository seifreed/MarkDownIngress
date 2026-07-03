#!/usr/bin/env python3
"""MarkDownIngress CLI."""

import sys

from markdown_ingress.cli_parsing import (
    create_batch_parser,
    create_benchmark_parser,
    create_compare_parser,
    create_ingest_parser,
    create_legacy_parser,
    create_standard_parser,
    is_legacy_mode,
)


def main():
    """Main CLI entry point."""
    if is_legacy_mode():
        parser = create_legacy_parser()
        args = parser.parse_args()
        from markdown_ingress.cli_commands import cmd_ingest

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
        from markdown_ingress.cli_commands import cmd_ingest

        _run_command(cmd_ingest, args)
    elif args.command == "batch":
        from markdown_ingress.cli_commands import cmd_batch

        _run_command(cmd_batch, args)
    elif args.command == "compare":
        from markdown_ingress.cli_commands import cmd_compare

        _run_command(cmd_compare, args)
    elif args.command == "benchmark":
        from markdown_ingress.cli_commands import cmd_benchmark

        _run_command(cmd_benchmark, args)


def _run_command(command, args):
    from markdown_ingress.cli_commands import run_cli_command

    return run_cli_command(command, args, command_name=getattr(command, "__name__", "command"))


if __name__ == "__main__":
    main()
