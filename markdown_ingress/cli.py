#!/usr/bin/env python3
"""
MarkDownIngress CLI
"""

import sys
import json
import asyncio
import argparse
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from markdown_ingress import ingest, generate_security_report, __version__
from markdown_ingress.core.scoring import Scorer
from markdown_ingress.core.batch import BatchProcessor
from markdown_ingress.core.config import load_config


console = Console()


def cmd_ingest(args):
    """Handle single URL ingestion"""
    # Determine strict mode
    strict = args.strict and not args.permissive
    
    # Determine mode
    mode = "render" if args.render else "fast"
    
    try:
        # Ingest content
        doc = ingest(
            url=args.url,
            mode=mode,
            strict=strict,
            model=args.model,
            timeout=args.timeout
        )
        
        # Output results
        if args.json:
            # JSON output
            output_data = {
                'markdown': doc.markdown if not args.no_content else None,
                'metadata': doc.metadata,
                'token_estimate': doc.token_estimate,
                'content_hash': doc.content_hash,
                'injection_score': doc.injection_score,
                'flags': doc.flags,
                'removed_elements': doc.removed_elements,
            }
            
            output = json.dumps(output_data, indent=2)
            
            if args.save:
                Path(args.save).write_text(output)
                console.print(f"[green]✓ JSON saved to {args.save}")
            else:
                print(output)
        else:
            # Rich formatted output
            scorer = Scorer()
            risk_level = doc.metadata.get('risk_level', 'unknown').upper()
            
            console.print()
            console.print("=" * 60)
            console.print(f"[bold]MarkDownIngress v{__version__} - Ingestion Report[/bold]")
            console.print("=" * 60)
            console.print()
            console.print(f"📄 [bold]Title:[/bold] {doc.metadata.get('title', 'N/A')}")
            console.print(f"🔗 [bold]URL:[/bold] {args.url}")
            console.print()
            
            # Token info
            token_savings = doc.metadata.get('token_savings', {})
            if token_savings:
                saved = token_savings.get('tokens_saved', 0)
                percentage = token_savings.get('percentage_saved', 0.0)
                console.print(f"✔ [green]Tokens: {doc.token_estimate}[/green]")
                console.print(f"  ↳ Saved: {saved:,} tokens ({percentage:.1f}% reduction)")
            else:
                console.print(f"✔ [green]Tokens: {doc.token_estimate}[/green]")
            console.print()
            
            # Security info
            score_color = "green" if doc.injection_score < 0.4 else "yellow" if doc.injection_score < 0.7 else "red"
            console.print(f"🔒 [bold]Injection Score:[/bold] [{score_color}]{doc.injection_score:.3f}[/{score_color}] ({risk_level})")
            
            if doc.flags:
                console.print(f"⚠️  [bold]Flags:[/bold] {', '.join(doc.flags)}")
            
            if doc.removed_elements.get('hidden_elements', 0) > 0:
                console.print(f"🗑️  [bold]Removed hidden elements:[/bold] {doc.removed_elements['hidden_elements']}")
            console.print()
            
            console.print(f"🔑 [bold]Hash:[/bold] {doc.content_hash}")
            console.print(f"⏱️  [bold]Fetch time:[/bold] {doc.metadata.get('fetch_time_ms', 0):.0f}ms")
            console.print()
            console.print("=" * 60)
            
            if not args.no_content:
                console.print("[bold]MARKDOWN OUTPUT[/bold]")
                console.print("=" * 60)
                console.print()
                console.print(doc.markdown)
            
            # Save if requested
            if args.save:
                Path(args.save).write_text(doc.markdown)
                console.print()
                console.print(f"[green]✓ Markdown saved to {args.save}")
        
        sys.exit(0)
        
    except Exception as e:
        console.print(f"[red]Error: {e}")
        sys.exit(1)


def cmd_batch(args):
    """Handle batch URL processing"""
    # Load URLs from file
    urls_file = Path(args.file)
    if not urls_file.exists():
        console.print(f"[red]Error: File not found: {args.file}")
        sys.exit(1)
    
    urls = [line.strip() for line in urls_file.read_text().splitlines() if line.strip() and not line.startswith('#')]
    
    if not urls:
        console.print("[yellow]No URLs found in file")
        sys.exit(0)
    
    console.print(f"[bold]Processing {len(urls)} URLs...[/bold]")
    console.print()
    
    # Configure batch processor
    mode = "render" if args.render else "fast"
    strict = args.strict and not args.permissive
    
    processor = BatchProcessor(
        mode=mode,
        strict=strict,
        max_concurrent=args.concurrent,
        timeout=args.timeout
    )
    
    # Process with progress bar
    async def process_all():
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Processing URLs...", total=len(urls))
            
            # Set up progress callback
            def on_progress(current, total, url):
                progress.update(task, completed=current)
            
            processor.on_progress = on_progress
            
            batch_result = await processor.process_batch_async(urls)
            
        return batch_result
    
    # Run async processing
    batch_result = asyncio.run(process_all())
    
    # Generate summary
    console.print()
    console.print("[bold]Batch Processing Summary[/bold]")
    console.print("=" * 60)
    
    successful = batch_result.successful
    failed = batch_result.failed
    
    console.print(f"✓ Successful: [green]{successful}[/green]")
    console.print(f"✗ Failed: [red]{failed}[/red]")
    console.print()
    
    # Results table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("URL", style="dim", width=40)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Score", justify="right", width=8)
    
    # Add successful documents
    for i, (url, doc) in enumerate(zip(urls, batch_result.documents)):
        if doc:
            url_display = url[:37] + "..." if len(url) > 40 else url
            status = "[green]✓"
            tokens = str(doc.token_estimate)
            score = f"{doc.injection_score:.2f}"
            table.add_row(url_display, status, tokens, score)
    
    # Add failed URLs
    for url, error in batch_result.errors.items():
        url_display = url[:37] + "..." if len(url) > 40 else url
        status = "[red]✗"
        tokens = "-"
        score = "-"
        table.add_row(url_display, status, tokens, score)
    
    console.print(table)
    console.print()
    
    # Save results if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if args.json:
            # Save as JSON
            output_data = {
                'summary': {
                    'total': batch_result.total,
                    'successful': batch_result.successful,
                    'failed': batch_result.failed,
                    'success_rate': batch_result.success_rate
                },
                'results': [
                    {
                        'url': urls[i],
                        'success': True,
                        'tokens': doc.token_estimate,
                        'injection_score': doc.injection_score,
                        'content_hash': doc.content_hash
                    }
                    for i, doc in enumerate(batch_result.documents) if doc
                ],
                'errors': batch_result.errors
            }
            output_path.write_text(json.dumps(output_data, indent=2))
        else:
            # Save markdown files to directory
            output_dir = output_path
            output_dir.mkdir(parents=True, exist_ok=True)
            
            for i, doc in enumerate(batch_result.documents):
                if doc:
                    filename = f"doc_{i+1:03d}.md"
                    (output_dir / filename).write_text(doc.markdown)
        
        console.print(f"[green]✓ Results saved to {args.output}")


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    
    subparsers = parser.add_subparsers(dest='command', help='Commands', required=False)
    
    # Ingest command (default, also support legacy single URL)
    ingest_parser = subparsers.add_parser('ingest', help='Ingest single URL')
    ingest_parser.add_argument('url', help='URL to ingest')
    ingest_parser.add_argument('--render', action='store_true', help='Use render mode (Playwright)')
    ingest_parser.add_argument('--strict', action='store_true', default=True, help='Enable strict security mode')
    ingest_parser.add_argument('--permissive', action='store_true', help='Disable strict mode')
    ingest_parser.add_argument('--model', default='gpt-4', help='LLM model for token estimation')
    ingest_parser.add_argument('--timeout', type=float, default=30.0, help='Request timeout in seconds')
    ingest_parser.add_argument('--json', action='store_true', help='Output as JSON')
    ingest_parser.add_argument('--no-content', action='store_true', help='Hide markdown content in output')
    ingest_parser.add_argument('--save', metavar='FILE', help='Save output to file')
    
    # Batch command
    batch_parser = subparsers.add_parser('batch', help='Process multiple URLs from file')
    batch_parser.add_argument('file', help='File containing URLs (one per line)')
    batch_parser.add_argument('--render', action='store_true', help='Use render mode')
    batch_parser.add_argument('--strict', action='store_true', default=True, help='Enable strict security mode')
    batch_parser.add_argument('--permissive', action='store_true', help='Disable strict mode')
    batch_parser.add_argument('--timeout', type=float, default=30.0, help='Request timeout per URL')
    batch_parser.add_argument('--concurrent', type=int, default=5, help='Max concurrent requests')
    batch_parser.add_argument('--json', action='store_true', help='Save results as JSON')
    batch_parser.add_argument('--output', '-o', help='Output directory (markdown) or file (json)')
    
    args = parser.parse_args()
    
    # Handle legacy mode (URL as first argument without subcommand)
    if args.command is None:
        # Check if first arg looks like a URL
        if len(sys.argv) > 1:
            first_arg = sys.argv[1]
            if first_arg.startswith('http://') or first_arg.startswith('https://'):
                # Create a new namespace with ingest arguments
                class IngestArgs:
                    def __init__(self):
                        self.url = first_arg
                        self.render = '--render' in sys.argv
                        self.strict = True
                        self.permissive = '--permissive' in sys.argv
                        self.model = 'gpt-4'
                        self.timeout = 30.0
                        self.json = '--json' in sys.argv
                        self.no_content = '--no-content' in sys.argv
                        self.save = None
                        
                        # Parse save argument
                        if '--save' in sys.argv:
                            idx = sys.argv.index('--save')
                            if idx + 1 < len(sys.argv):
                                self.save = sys.argv[idx + 1]
                        
                        # Parse model
                        if '--model' in sys.argv:
                            idx = sys.argv.index('--model')
                            if idx + 1 < len(sys.argv):
                                self.model = sys.argv[idx + 1]
                        
                        # Parse timeout
                        if '--timeout' in sys.argv:
                            idx = sys.argv.index('--timeout')
                            if idx + 1 < len(sys.argv):
                                self.timeout = float(sys.argv[idx + 1])
                
                legacy_args = IngestArgs()
                cmd_ingest(legacy_args)
            else:
                parser.print_help()
                sys.exit(0)
        else:
            parser.print_help()
            sys.exit(0)
    elif args.command == 'ingest':
        cmd_ingest(args)
    elif args.command == 'batch':
        cmd_batch(args)


if __name__ == '__main__':
    main()
