#!/usr/bin/env python3
"""MarkDownIngress CLI."""

import sys

from markdown_ingress.cli_commands import (
    cmd_batch,
    cmd_benchmark,
    cmd_compare,
    cmd_ingest,
)
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
