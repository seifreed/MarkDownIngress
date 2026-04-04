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

from markdown_ingress import __version__, compare_extractors, ingest, ingest_many_async
from markdown_ingress.api_runtime import UNSET
from markdown_ingress.application.batch import BatchProcessor
from markdown_ingress.cli_parsing import determine_mode, load_runtime_config, prepare_ingest_params
from markdown_ingress.cli_support import (
    _build_batch_rows,
    console,
    create_batch_results_table,
    display_batch_summary,
    display_rich_output,
    load_domain_policies,
    load_urls_from_file,
    save_batch_results,
    save_json_output,
    save_markdown_output,
)
from markdown_ingress.core.benchmark import Benchmark

_logger = logging.getLogger(__name__)


def build_json_output(doc, args):
    """Build JSON output from document."""
    return {
        "markdown": doc.markdown if not args.no_content else None,
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
        _logger.exception("Error in cmd_ingest for %s", getattr(args, "url", "unknown"))
        sys.exit(1)


def create_batch_processor(args):
    """Create and configure batch processor."""
    runtime_config = load_runtime_config(args)
    mode = determine_mode(args) or (runtime_config.mode if runtime_config is not None else "auto")
    # Handle strict flag: distinguish between explicit False, explicit True, and not set
    # --strict sets strict=True, --permissive sets strict=False
    # If neither is set, use runtime_config.strict or None (let config resolution handle default)
    if getattr(args, "permissive", False):
        strict = False
    elif getattr(args, "strict", False):
        strict = True
    elif runtime_config is not None:
        strict = runtime_config.strict
    else:
        strict = None  # Let downstream API decide default

    # Validate and apply timeout with minimum
    timeout = args.timeout if args.timeout is not None else (
        runtime_config.batch_timeout if runtime_config is not None else 30.0
    )
    if timeout is not None and timeout <= 0:
        raise ValueError(f"timeout must be > 0, got {timeout}")

    # Validate and apply concurrent with minimum
    concurrent = args.concurrent if args.concurrent is not None else (
        runtime_config.batch_max_concurrent if runtime_config is not None else 5
    )
    if concurrent is not None and concurrent < 1:
        raise ValueError(f"concurrent must be >= 1, got {concurrent}")

    model = args.model if getattr(args, "model", None) is not None else (
        runtime_config.model if runtime_config is not None else "gpt-4"
    )
    return BatchProcessor(mode=mode, strict=strict, model=model, max_concurrent=concurrent, timeout=timeout)


async def process_batch_with_progress(processor, urls):
    """Process URLs with progress bar."""
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Processing URLs...", total=len(urls))

        def on_progress(current, total, url):
            progress.update(task, completed=current)

        processor.on_progress = on_progress
        return await processor.process_batch_async(urls)


def _get_bool_flag(args, positive_flag: str, negative_flag: str) -> bool | None:
    """Get boolean flag value with symmetric positive/negative handling.

    Returns True if positive flag is set, False if negative flag is set, None otherwise.
    """
    if getattr(args, positive_flag, False):
        return True
    if getattr(args, negative_flag, False):
        return False
    return None


def _resolve_output_format(args, config_output_format: str | None) -> str:
    """Resolve the effective output format with CLI flags taking precedence."""
    if getattr(args, "json", False):
        return "json"
    return config_output_format or "text"


def _build_batch_json_output(urls: list[str], batch_result) -> dict:
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
            {
                "url": row["url"],
                "success": row["document"] is not None,
                "tokens": row["document"].token_estimate if row["document"] is not None else None,
                "injection_score": row["document"].injection_score if row["document"] is not None else None,
                "content_hash": row["document"].content_hash if row["document"] is not None else None,
                "metadata": row["document"].metadata if row["document"] is not None else None,
                "chunks": row["document"].chunks if row["document"] is not None else None,
                "structured_blocks": row["document"].structured_blocks if row["document"] is not None else None,
                "error": row["error"],
            }
            for row in rows
        ],
        "errors": [
            {
                "index": error_item.index,
                "url": error_item.url,
                "error": error_item.error,
            }
            for error_item in getattr(batch_result, "error_items", getattr(batch_result, "errors", []))
            if hasattr(error_item, "index") and hasattr(error_item, "url") and hasattr(error_item, "error")
        ],
        "errors_by_url": getattr(batch_result, "errors_by_url", {}),
    }


async def ingest_many_with_progress(args, urls):
    """Run batch ingestion through the public API with progress updates."""
    runtime_config = load_runtime_config(args)
    strict = False if getattr(args, "permissive", False) else (
        True if getattr(args, "strict", False) else (runtime_config.strict if runtime_config is not None else None)
    )
    mode = determine_mode(args) or (runtime_config.mode if runtime_config is not None else None)
    timeout = args.timeout if args.timeout is not None else (
        runtime_config.batch_timeout if runtime_config is not None else None
    )
    concurrent = args.concurrent if getattr(args, "concurrent", None) is not None else (
        runtime_config.batch_max_concurrent if runtime_config is not None else 5
    )
    model = args.model if getattr(args, "model", None) is not None else (
        runtime_config.model if runtime_config is not None else None
    )

    # Handle screenshot: use UNSET sentinel when not provided
    screenshot = UNSET
    if hasattr(args, "screenshot") and args.screenshot:
        screenshot = args.screenshot

    # Boolean flags with symmetric handling
    extract_metadata = _get_bool_flag(args, "metadata", "no_metadata")
    extract_links = _get_bool_flag(args, "links", "no_links")
    advanced_security = _get_bool_flag(args, "advanced_security", "no_advanced_security")
    use_llm = _get_bool_flag(args, "use_llm", "no_llm")

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Processing URLs...", total=len(urls))

        def on_progress(current, total, url):
            progress.update(task, completed=current)

        return await ingest_many_async(
            urls,
            mode=mode,
            strict=strict,
            model=model,
            timeout=timeout,
            screenshot=screenshot,
            extract_metadata=extract_metadata,
            extract_links=extract_links,
            advanced_security=advanced_security,
            use_llm=use_llm,
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


def cmd_batch(args):
    """Handle batch URL processing."""
    urls = load_urls_from_file(args.file)
    runtime_config = load_runtime_config(args)
    output_format = _resolve_output_format(args, runtime_config.output_format)
    if output_format != "json":
        console.print(f"[bold]Processing {len(urls)} URLs...[/bold]")
        console.print()
    batch_result = asyncio.run(ingest_many_with_progress(args, urls))
    if output_format == "json":
        if args.output:
            save_args = Namespace(**vars(args))
            save_args.json = True
            save_batch_results(save_args, urls, batch_result)
        else:
            print(json.dumps(_build_batch_json_output(urls, batch_result), indent=2))
        return

    display_batch_summary(batch_result, urls)
    table = create_batch_results_table(urls, batch_result)
    console.print(table)
    console.print()
    save_batch_results(args, urls, batch_result)


def cmd_compare(args):
    """Compare extractor quality/security on a local HTML file."""
    html = Path(args.file).read_text()
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


def cmd_benchmark(args):
    """Run benchmark report with optional extractor comparison."""
    urls = load_urls_from_file(args.file)
    bench = Benchmark(model=args.model or "gpt-4")
    results = bench.run_batch(
        urls,
        mode=determine_mode(args) or "auto",
        iterations=args.iterations,
        compare_extractors_enabled=args.compare_extractors,
    )
    report = bench.generate_report(results)
    if args.output:
        Path(args.output).write_text(report)
    else:
        console.print(report)
