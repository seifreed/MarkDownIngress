#!/usr/bin/env python3
"""
MarkDownIngress CLI
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from markdown_ingress import __version__, ingest
from markdown_ingress.core.batch import BatchProcessor
from markdown_ingress.core.scoring import Scorer

# Force UTF-8 encoding for console output (fixes Windows emoji issues)
console = Console(force_terminal=True)


@dataclass
class IngestArgs:
    """Arguments for ingest command (used in legacy mode)"""
    url: str
    render: bool = False
    fast: bool = False
    strict: bool = True
    permissive: bool = False
    model: str = "gpt-4"
    timeout: float = 30.0
    json: bool = False
    no_content: bool = False
    save: str = None
    screenshot: str = None
    no_metadata: bool = False
    no_links: bool = False
    advanced_security: bool = False
    use_llm: bool = False


def _determine_mode(args):
    """Determine ingestion mode from args"""
    if hasattr(args, "fast") and args.fast:
        return "fast"
    elif hasattr(args, "render") and args.render:
        return "render"
    return "auto"


def _prepare_ingest_params(args):
    """Prepare parameters for ingest call"""
    strict = args.strict and not args.permissive
    mode = _determine_mode(args)
    
    screenshot = None
    if hasattr(args, "screenshot") and args.screenshot:
        screenshot = args.screenshot if args.screenshot != "True" else True
    
    extract_metadata = not getattr(args, "no_metadata", False)
    extract_links = not getattr(args, "no_links", False)
    advanced_security = getattr(args, "advanced_security", False)
    use_llm = getattr(args, "use_llm", False)
    
    return {
        "url": args.url,
        "mode": mode,
        "strict": strict,
        "model": args.model,
        "timeout": args.timeout,
        "screenshot": screenshot,
        "extract_metadata": extract_metadata,
        "extract_links": extract_links,
        "advanced_security": advanced_security,
        "use_llm": use_llm,
    }


def _build_json_output(doc, args):
    """Build JSON output from document"""
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
    }


def _display_header(args):
    """Display report header"""
    console.print()
    console.print("=" * 60)
    console.print(f"[bold]MarkDownIngress v{__version__} - Ingestion Report[/bold]")
    console.print("=" * 60)
    console.print()


def _display_basic_info(doc, args):
    """Display basic document information"""
    console.print(f"[bold]Title:[/bold] {doc.metadata.get('title', 'N/A')}")
    console.print(f"🔗 [bold]URL:[/bold] {args.url}")
    console.print()


def _display_token_info(doc):
    """Display token statistics"""
    token_savings = doc.metadata.get("token_savings", {})
    if token_savings:
        saved = token_savings.get("tokens_saved", 0)
        percentage = token_savings.get("percentage_saved", 0.0)
        console.print(f"[green]Tokens: {doc.token_estimate}[/green]")
        console.print(f"  Saved: {saved:,} tokens ({percentage:.1f}% reduction)")
    else:
        console.print(f"[green]Tokens: {doc.token_estimate}[/green]")
    console.print()


def _display_security_info(doc):
    """Display security information"""
    risk_level = doc.metadata.get("risk_level", "unknown").upper()
    score_color = (
        "green"
        if doc.injection_score < 0.4
        else "yellow" if doc.injection_score < 0.7 else "red"
    )
    console.print(
        f"🔒 [bold]Injection Score:[/bold] [{score_color}]{doc.injection_score:.3f}[/{score_color}] ({risk_level})"
    )
    
    if doc.flags:
        console.print(f"⚠️  [bold]Flags:[/bold] {', '.join(doc.flags)}")
    
    if doc.removed_elements.get("hidden_elements", 0) > 0:
        console.print(
            f"🗑️  [bold]Removed hidden elements:[/bold] {doc.removed_elements['hidden_elements']}"
        )
    console.print()


def _display_metadata(doc):
    """Display document metadata"""
    console.print(f"🔑 [bold]Hash:[/bold] {doc.content_hash}")
    console.print(
        f"⏱️  [bold]Fetch time:[/bold] {doc.metadata.get('fetch_time_ms', 0):.0f}ms"
    )
    console.print()
    console.print("=" * 60)


def _display_content(doc, args):
    """Display markdown content"""
    if not args.no_content:
        console.print("[bold]MARKDOWN OUTPUT[/bold]")
        console.print("=" * 60)
        console.print()
        console.print(doc.markdown)


def _display_rich_output(doc, args):
    """Display rich formatted output"""
    _display_header(args)
    _display_basic_info(doc, args)
    _display_token_info(doc)
    _display_security_info(doc)
    _display_metadata(doc)
    _display_content(doc, args)


def _save_json_output(output_data, args):
    """Save JSON output to file or print"""
    output = json.dumps(output_data, indent=2)
    if args.save:
        Path(args.save).write_text(output)
        console.print(f"[green]JSON saved to {args.save}")
    else:
        print(output)


def _save_markdown_output(doc, args):
    """Save markdown output if requested"""
    if args.save:
        Path(args.save).write_text(doc.markdown)
        console.print()
        console.print(f"[green]Markdown saved to {args.save}")


def cmd_ingest(args):
    """Handle single URL ingestion"""
    try:
        params = _prepare_ingest_params(args)
        doc = ingest(**params)
        
        if args.json:
            output_data = _build_json_output(doc, args)
            _save_json_output(output_data, args)
        else:
            _display_rich_output(doc, args)
            _save_markdown_output(doc, args)
        
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Error: {e}")
        sys.exit(1)


def _load_urls_from_file(filepath):
    """Load and parse URLs from file"""
    urls_file = Path(filepath)
    if not urls_file.exists():
        console.print(f"[red]Error: File not found: {filepath}")
        sys.exit(1)
    
    urls = [
        line.strip()
        for line in urls_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    
    if not urls:
        console.print("[yellow]No URLs found in file")
        sys.exit(0)
    
    return urls


def _create_batch_processor(args):
    """Create and configure batch processor"""
    mode = _determine_mode(args)
    strict = args.strict and not args.permissive
    
    return BatchProcessor(
        mode=mode, strict=strict, max_concurrent=args.concurrent, timeout=args.timeout
    )


async def _process_batch_with_progress(processor, urls):
    """Process URLs with progress bar"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Processing URLs...", total=len(urls))
        
        def on_progress(current, total, url):
            progress.update(task, completed=current)
        
        processor.on_progress = on_progress
        batch_result = await processor.process_batch_async(urls)
    
    return batch_result


def _display_batch_summary(batch_result):
    """Display batch processing summary"""
    console.print()
    console.print("[bold]Batch Processing Summary[/bold]")
    console.print("=" * 60)
    console.print(f"Successful: [green]{batch_result.successful}[/green]")
    console.print(f"✗ Failed: [red]{batch_result.failed}[/red]")
    console.print()


def _create_batch_results_table(urls, batch_result):
    """Create results table for batch processing"""
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("URL", style="dim", width=40)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Score", justify="right", width=8)
    
    for i, (url, doc) in enumerate(zip(urls, batch_result.documents)):
        if doc:
            url_display = url[:37] + "..." if len(url) > 40 else url
            table.add_row(url_display, "[green]OK", str(doc.token_estimate), f"{doc.injection_score:.2f}")
    
    for url, error in batch_result.errors.items():
        url_display = url[:37] + "..." if len(url) > 40 else url
        table.add_row(url_display, "[red]✗", "-", "-")
    
    return table


def _save_batch_json(output_path, urls, batch_result):
    """Save batch results as JSON"""
    output_data = {
        "summary": {
            "total": batch_result.total,
            "successful": batch_result.successful,
            "failed": batch_result.failed,
            "success_rate": batch_result.success_rate,
        },
        "results": [
            {
                "url": urls[i],
                "success": True,
                "tokens": doc.token_estimate,
                "injection_score": doc.injection_score,
                "content_hash": doc.content_hash,
                "metadata": doc.metadata,
            }
            for i, doc in enumerate(batch_result.documents)
            if doc
        ],
        "errors": batch_result.errors,
    }
    output_path.write_text(json.dumps(output_data, indent=2))


def _save_batch_markdown(output_dir, batch_result):
    """Save batch results as markdown files"""
    output_dir.mkdir(parents=True, exist_ok=True)
    for i, doc in enumerate(batch_result.documents):
        if doc:
            filename = f"doc_{i+1:03d}.md"
            (output_dir / filename).write_text(doc.markdown)


def _save_batch_results(args, urls, batch_result):
    """Save batch results to file"""
    if not args.output:
        return
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if args.json:
        _save_batch_json(output_path, urls, batch_result)
    else:
        _save_batch_markdown(output_path, batch_result)
    
    console.print(f"[green]Results saved to {args.output}")


def cmd_batch(args):
    """Handle batch URL processing"""
    urls = _load_urls_from_file(args.file)
    
    console.print(f"[bold]Processing {len(urls)} URLs...[/bold]")
    console.print()
    
    processor = _create_batch_processor(args)
    batch_result = asyncio.run(_process_batch_with_progress(processor, urls))
    
    _display_batch_summary(batch_result)
    table = _create_batch_results_table(urls, batch_result)
    console.print(table)
    console.print()
    
    _save_batch_results(args, urls, batch_result)


def _add_common_ingest_args(parser):
    """Add common ingest arguments to a parser"""
    parser.add_argument(
        "--auto", action="store_true", default=True, help="Auto-detect mode (default)"
    )
    parser.add_argument(
        "--render", action="store_true", help="Force render mode (Playwright)"
    )
    parser.add_argument("--fast", action="store_true", help="Force fast mode (HTTP only)")
    parser.add_argument(
        "--strict", action="store_true", default=True, help="Enable strict security mode"
    )
    parser.add_argument("--permissive", action="store_true", help="Disable strict mode")
    parser.add_argument("--model", default="gpt-4", help="LLM model for token estimation")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Request timeout in seconds"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--no-content", action="store_true", help="Hide markdown content in output"
    )
    parser.add_argument("--save", metavar="FILE", help="Save output to file")
    parser.add_argument(
        "--screenshot",
        nargs="?",
        const="True",
        help="Capture screenshot (render mode only). Optionally specify path.",
    )
    parser.add_argument(
        "--no-metadata", action="store_true", help="Disable metadata extraction"
    )
    parser.add_argument("--no-links", action="store_true", help="Disable link extraction")
    parser.add_argument(
        "--advanced-security",
        action="store_true",
        help="Enable Nova-tracer advanced injection detection (requires nova-hunting)",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable LLM-based detection tier (slow but most accurate, requires ANTHROPIC_API_KEY)",
    )


def _create_ingest_parser(subparsers):
    """Create ingest subcommand parser"""
    ingest_parser = subparsers.add_parser("ingest", help="Ingest single URL")
    ingest_parser.add_argument("url", help="URL to ingest")
    _add_common_ingest_args(ingest_parser)


def _create_batch_parser(subparsers):
    """Create batch subcommand parser"""
    batch_parser = subparsers.add_parser("batch", help="Process multiple URLs from file")
    batch_parser.add_argument("file", help="File containing URLs (one per line)")
    batch_parser.add_argument(
        "--auto", action="store_true", default=True, help="Auto-detect mode (default)"
    )
    batch_parser.add_argument(
        "--render", action="store_true", help="Force render mode for all URLs"
    )
    batch_parser.add_argument("--fast", action="store_true", help="Force fast mode for all URLs")
    batch_parser.add_argument(
        "--strict", action="store_true", default=True, help="Enable strict security mode"
    )
    batch_parser.add_argument("--permissive", action="store_true", help="Disable strict mode")
    batch_parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout per URL")
    batch_parser.add_argument("--concurrent", type=int, default=5, help="Max concurrent requests")
    batch_parser.add_argument("--json", action="store_true", help="Save results as JSON")
    batch_parser.add_argument("--output", "-o", help="Output directory (markdown) or file (json)")


def _is_legacy_mode():
    """Check if we're in legacy mode (direct URL without subcommand)"""
    return (len(sys.argv) > 1 and 
            (sys.argv[1].startswith("http://") or sys.argv[1].startswith("https://")))


def _create_legacy_parser():
    """Create parser for legacy mode"""
    legacy_parser = argparse.ArgumentParser(
        description="MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine"
    )
    legacy_parser.add_argument("url", help="URL to ingest")
    _add_common_ingest_args(legacy_parser)
    return legacy_parser


def _create_standard_parser():
    """Create standard parser with subcommands"""
    parser = argparse.ArgumentParser(
        description="MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main():
    """Main CLI entry point"""
    if _is_legacy_mode():
        parser = _create_legacy_parser()
        args = parser.parse_args()
        cmd_ingest(args)
        return
    
    parser = _create_standard_parser()
    subparsers = parser.add_subparsers(dest="command", help="Commands", required=False)
    
    _create_ingest_parser(subparsers)
    _create_batch_parser(subparsers)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    elif args.command == "ingest":
        cmd_ingest(args)
    elif args.command == "batch":
        cmd_batch(args)


if __name__ == "__main__":
    main()
