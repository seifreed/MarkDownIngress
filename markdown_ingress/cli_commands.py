"""Command implementations for the CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from argparse import Namespace
from pathlib import Path

from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
from rich.table import Table

import markdown_ingress._version as _version
import markdown_ingress.api as _api
from markdown_ingress.cli_parsing import (
    _extract_cli_feature_flags,
    determine_mode,
    load_runtime_config,
    prepare_ingest_params,
)
from markdown_ingress.cli_support import (
    _build_batch_rows,
    _iter_batch_error_items,
    batch_document_json_row,
    console,
    create_batch_results_table,
    display_batch_summary,
    display_rich_output,
    document_json_fields,
    load_domain_policies,
    load_urls_from_file,
    save_batch_results,
    save_json_output,
    save_markdown_output,
)
from markdown_ingress.core.benchmark import Benchmark
from markdown_ingress.runtime_helpers import UNSET

_logger = logging.getLogger(__name__)
__version__ = _version.VERSION


def build_json_output(doc, args):
    """Build JSON output from document."""
    return {
        "markdown": doc.markdown if not args.no_content else None,
        **document_json_fields(doc),
    }


def cmd_ingest(args):
    """Handle single URL ingestion."""
    try:
        runtime_config = load_runtime_config(args)
        params = prepare_ingest_params(args, runtime_config=runtime_config)
        doc = ingest(**params)
        output_format = _resolve_output_format(args, runtime_config.output_format)
        if output_format == "json":
            output_data = build_json_output(doc, args)
            save_json_output(output_data, args)
        elif output_format == "markdown":
            if not args.no_content:
                print(doc.markdown)
            save_markdown_output(doc, args)
        else:
            display_rich_output(doc, args, __version__)
            save_markdown_output(doc, args)
        sys.exit(0)
    except Exception as exc:
        console.print(f"[red]Error: {exc}")
        _logger.debug(
            "Error in cmd_ingest for %s",
            getattr(args, "url", "unknown"),
            exc_info=True,
        )
        sys.exit(1)


def _resolve_output_format(args, config_output_format: str | None) -> str:
    """Resolve the effective output format with CLI flags taking precedence."""
    if getattr(args, "json", False):
        return "json"
    return config_output_format or "text"


def _build_batch_json_output(
    urls: list[str],
    batch_result,
    *,
    no_content: bool = False,
) -> dict:
    """Serialize batch results for JSON output."""
    rows = _build_batch_rows(urls, batch_result)
    successful = sum(1 for row in rows if row["document"] is not None)
    failed = sum(1 for row in rows if row["document"] is None)
    return {
        "summary": {
            "total": len(rows),
            "successful": successful,
            "failed": failed,
            "success_rate": (successful / len(rows) * 100) if rows else 0.0,
        },
        "results": [
            (
                batch_document_json_row(row, no_content=no_content)
                if row["document"] is not None
                else {
                    "url": row["url"],
                    "success": False,
                    "error": row["error"],
                }
            )
            for row in rows
        ],
        "errors": [
            {
                "index": error_item.index,
                "url": error_item.url,
                "error": error_item.error,
            }
            for error_item in _iter_batch_error_items(batch_result)
        ],
        "errors_by_url": getattr(batch_result, "errors_by_url", {}),
    }


async def ingest_many_with_progress(args, urls, *, show_progress: bool = True):
    """Run batch ingestion through the public API with progress updates."""
    from markdown_ingress.application.bootstrap import register_all_factories

    register_all_factories()
    runtime_config = load_runtime_config(args)
    runtime_ingest_config = (
        runtime_config.to_ingest_config() if runtime_config is not None else None
    )
    strict = (
        False
        if getattr(args, "permissive", False)
        else (True if getattr(args, "strict", False) else None)
    )
    mode = determine_mode(args)
    timeout = args.timeout if args.timeout is not None else UNSET
    concurrent = args.concurrent if getattr(args, "concurrent", None) is not None else UNSET
    model = args.model if getattr(args, "model", None) is not None else None

    # Handle screenshot: use UNSET sentinel when not provided
    screenshot = UNSET
    if hasattr(args, "screenshot") and args.screenshot:
        screenshot = args.screenshot

    async def run(on_progress=None):
        return await ingest_many_async(
            urls,
            config=runtime_ingest_config,
            mode=mode,
            strict=strict,
            model=model,
            timeout=timeout,
            screenshot=screenshot,
            **_extract_cli_feature_flags(args),
            output_profile=getattr(args, "output_profile", None),
            extract_blocks=getattr(args, "extract_blocks", None),
            chunking_strategy=getattr(args, "chunking_strategy", None),
            chunk_size=getattr(args, "chunk_size", None),
            chunk_overlap=getattr(args, "chunk_overlap", None),
            render_cost_budget=getattr(args, "render_cost_budget", None),
            domain_policies=load_domain_policies(args),
            max_concurrent=concurrent,
            on_progress=on_progress,
        )

    if not show_progress:
        return await run()

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Processing URLs...", total=len(urls))

        def on_progress(current, _total, _url):
            progress.update(task, completed=current)

        return await run(on_progress)


def cmd_batch(args):
    """Handle batch URL processing."""
    urls = load_urls_from_file(args.file)
    runtime_config = load_runtime_config(args)
    output_format = _resolve_output_format(args, runtime_config.output_format)
    if output_format != "json":
        console.print(f"[bold]Processing {len(urls)} URLs...[/bold]")
        console.print()
    batch_result = asyncio.run(
        ingest_many_with_progress(args, urls, show_progress=False)
        if output_format == "json"
        else ingest_many_with_progress(args, urls)
    )
    if output_format == "json":
        if args.output:
            save_args = Namespace(**vars(args))
            save_args.json = True
            save_batch_results(save_args, urls, batch_result)
        else:
            print(
                json.dumps(
                    _build_batch_json_output(
                        urls,
                        batch_result,
                        no_content=getattr(args, "no_content", False),
                    ),
                    indent=2,
                )
            )
        return

    if output_format == "markdown":
        rows = _build_batch_rows(urls, batch_result)
        for row in rows:
            if row["document"] is not None and not getattr(args, "no_content", False):
                print(row["document"].markdown)
        save_batch_results(args, urls, batch_result)
        return

    display_batch_summary(batch_result, urls)
    table = create_batch_results_table(urls, batch_result)
    console.print(table)
    console.print()
    save_batch_results(args, urls, batch_result)


def cmd_compare(args):
    """Compare extractor quality/security on a local HTML file."""
    html = Path(args.file).read_text(encoding="utf-8")
    result = compare_extractors(html, model=args.model or "gpt-4")
    if args.json:
        print(json.dumps(result, indent=2))
        return
    console.print("[bold]Extractor Comparison[/bold]")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Extractor")
    table.add_column("Available")
    table.add_column("Length", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Score", justify="right")
    for name, data in result.items():
        table.add_row(
            name,
            "yes" if data["available"] else "no",
            str(data["markdown_length"]),
            str(data["token_estimate"]),
            f"{data['injection_score']:.3f}",
        )
    console.print(table)


ingest = _api.ingest
ingest_many_async = _api.ingest_many_async
compare_extractors = _api.compare_extractors


def cmd_benchmark(args):
    """Run benchmark report with optional extractor comparison."""
    urls = load_urls_from_file(args.file)
    fetcher_factory = None
    try:
        from markdown_ingress.adapters.extractors.comparison import compare_extractors as _cmp_fn

        _compare_fn = _cmp_fn
    except ImportError:
        _compare_fn = None
    if args.compare_extractors:
        try:
            from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

            fetcher_factory = Fetcher
        except ImportError:
            fetcher_factory = None
    bench = Benchmark(
        model=args.model or "gpt-4",
        fetcher_factory=fetcher_factory,
        compare_fn=_compare_fn,
    )
    results = bench.run_batch(
        urls,
        mode=determine_mode(args) or "auto",
        iterations=args.iterations,
        compare_extractors_enabled=args.compare_extractors,
    )
    for failed_url, reason in bench.failures:
        console.print(f"[yellow]Skipped {failed_url}: {reason}")
    if not results:
        raise RuntimeError("No benchmark results")
    report = bench.generate_report(results)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    else:
        console.print(report)
