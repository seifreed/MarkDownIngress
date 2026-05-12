"""Display and file helpers for the CLI."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from rich.console import Console
from rich.table import Table

# Configure console based on actual terminal support, not just platform
# - Use force_terminal only when stdout is actually a TTY (not piped/redirected)
# - Enable legacy Windows mode for better compatibility on Windows
# - Respect NO_COLOR environment variable for color output
_console_force_terminal = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
_console_no_color = (
    os.getenv("NO_COLOR") is not None
    or (hasattr(sys.stdout, "isatty") and not sys.stdout.isatty())
    or (hasattr(sys.stderr, "isatty") and not sys.stderr.isatty())
)

console = Console(
    force_terminal=_console_force_terminal,
    legacy_windows=sys.platform == "win32",
    no_color=_console_no_color,
)


def document_json_fields(doc) -> dict:
    """Serialize non-CLI-specific document fields for JSON outputs."""
    return {
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


def batch_document_json_row(row: dict, *, no_content: bool = False) -> dict:
    """Serialize one successful batch row for JSON outputs."""
    document = row["document"]
    return {
        "url": row["url"],
        "success": True,
        "markdown": None if no_content else document.markdown,
        **document_json_fields(document),
        "tokens": document.token_estimate,
        "error": row["error"],
    }


def display_header(version: str) -> None:
    console.print()
    console.print("=" * 60)
    console.print(f"[bold]MarkDownIngress v{version} - Ingestion Report[/bold]")
    console.print("=" * 60)
    console.print()


def display_basic_info(doc, url: str) -> None:
    console.print(f"[bold]Title:[/bold] {doc.metadata.get('title', 'N/A')}")
    console.print(f"[bold]URL:[/bold] {url}")
    console.print()


def display_token_info(doc) -> None:
    token_savings = doc.metadata.get("token_savings", {})
    if token_savings:
        saved = token_savings.get("saved_tokens", 0)
        percentage = token_savings.get("savings_percent", 0.0)
        console.print(f"[green]Tokens: {doc.token_estimate}[/green]")
        console.print(f"  Saved: {saved:,} tokens ({percentage:.1f}% reduction)")
    else:
        console.print(f"[green]Tokens: {doc.token_estimate}[/green]")
    console.print()


def display_security_info(doc) -> None:
    risk_level = doc.metadata.get("risk_level", "unknown").upper()
    score_color = (
        "green" if doc.injection_score < 0.4 else "yellow" if doc.injection_score < 0.7 else "red"
    )
    console.print(
        f"[bold]Injection Score:[/bold] [{score_color}]{doc.injection_score:.3f}"
        f"[/{score_color}] ({risk_level})"
    )
    if doc.flags:
        console.print(f"[bold]Flags:[/bold] {', '.join(doc.flags)}")
    if doc.removed_elements.get("hidden_elements", 0) > 0:
        console.print(
            f"[bold]Removed hidden elements:[/bold] {doc.removed_elements['hidden_elements']}"
        )
    console.print()


def display_metadata(doc) -> None:
    console.print(f"[bold]Hash:[/bold] {doc.content_hash}")
    console.print(f"[bold]Fetch time:[/bold] {doc.metadata.get('fetch_time_ms', 0):.0f}ms")
    if doc.metadata.get("language"):
        console.print(f"[bold]Language:[/bold] {doc.metadata.get('language')}")
    if doc.metadata.get("output_profile"):
        console.print(f"[bold]Profile:[/bold] {doc.metadata.get('output_profile')}")
    console.print()
    console.print("=" * 60)


def display_content(doc, args) -> None:
    if not args.no_content:
        console.print("[bold]MARKDOWN OUTPUT[/bold]")
        console.print("=" * 60)
        console.print()
        console.print(doc.markdown)
    if getattr(args, "show_blocks", False) and doc.structured_blocks:
        console.print()
        console.print("[bold]STRUCTURED BLOCKS[/bold]")
        console.print("=" * 60)
        for block in doc.structured_blocks:
            console.print(
                f"{block['ordinal']:03d} {block['block_type']} {block.get('level') or '-'}"
            )
    if getattr(args, "show_chunks", False) and doc.chunks:
        console.print()
        console.print("[bold]CHUNKS[/bold]")
        console.print("=" * 60)
        for chunk in doc.chunks:
            console.print(
                f"{chunk['chunk_id']}: blocks={chunk['block_ordinals']} "
                f"tokens={chunk['token_estimate']}"
            )
    if getattr(args, "show_observability", False) and doc.observability:
        console.print()
        console.print("[bold]OBSERVABILITY[/bold]")
        console.print("=" * 60)
        console.print(json.dumps(doc.observability, indent=2))


def display_rich_output(doc, args, version: str) -> None:
    display_header(version)
    display_basic_info(doc, args.url)
    display_token_info(doc)
    display_security_info(doc)
    display_metadata(doc)
    display_content(doc, args)


def save_json_output(output_data, args) -> None:
    output = json.dumps(output_data, indent=2)
    if args.save:
        Path(args.save).write_text(output)
        console.print(f"[green]JSON saved to {args.save}")
    else:
        print(output)


def save_markdown_output(doc, args) -> None:
    if args.save:
        Path(args.save).write_text(doc.markdown)
        console.print()
        console.print(f"[green]Markdown saved to {args.save}")


def load_domain_policies(args):
    """Load domain policies from args."""
    policy_file = getattr(args, "domain_policy_file", None)
    inline_policies = getattr(args, "domain_policy", None) or []
    if not policy_file and not inline_policies:
        return None

    policies = []
    if policy_file:
        try:
            content = Path(policy_file).read_text()
            loaded = json.loads(content)
        except OSError as exc:
            console.print(f"[red]Error: Could not read domain policy file {policy_file}: {exc}")
            raise SystemExit(1) from None
        except json.JSONDecodeError as exc:
            console.print(f"[red]Error: Invalid JSON in domain policy file {policy_file}: {exc}")
            raise SystemExit(1) from None
        if isinstance(loaded, dict):
            policies.append(loaded)
        elif isinstance(loaded, list):
            for index, item in enumerate(loaded):
                if not isinstance(item, Mapping):
                    console.print(
                        "[red]Error: Domain policy file array entries must be JSON objects "
                        f"(invalid entry at index {index}: {type(item).__name__})"
                    )
                    raise SystemExit(1)
                policies.append(dict(item))
        else:
            console.print("[red]Error: Domain policy file must contain a JSON object or array")
            raise SystemExit(1)
    for inline in inline_policies:
        try:
            loaded_inline = json.loads(inline)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Error: Invalid JSON in --domain-policy: {exc}")
            raise SystemExit(1) from None
        if not isinstance(loaded_inline, Mapping):
            console.print("[red]Error: --domain-policy must decode to a JSON object")
            raise SystemExit(1)
        policies.append(dict(loaded_inline))
    return policies


def load_urls_from_file(filepath: str) -> list[str]:
    urls_file = Path(filepath)
    if not urls_file.exists():
        console.print(f"[red]Error: File not found: {filepath}")
        raise SystemExit(1)
    urls = [
        line.strip()
        for line in urls_file.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not urls:
        console.print("[yellow]No URLs found in file")
        raise SystemExit(0)
    return urls


def display_batch_summary(batch_result, urls: list[str] | None = None) -> None:
    rows = _build_batch_rows(urls, batch_result) if urls is not None else None
    successful = (
        sum(1 for row in rows if row["document"] is not None)
        if rows is not None
        else batch_result.successful
    )
    failed = (
        sum(1 for row in rows if row["document"] is None)
        if rows is not None
        else batch_result.failed
    )
    console.print()
    console.print("[bold]Batch Processing Summary[/bold]")
    console.print("=" * 60)
    console.print(f"Successful: [green]{successful}[/green]")
    console.print(f"Failed: [red]{failed}[/red]")
    console.print()


def _build_batch_rows(urls: list[str], batch_result) -> list[dict]:
    raw_error_items = getattr(batch_result, "error_items", getattr(batch_result, "errors", []))
    error_by_index = {
        item.index: item.error
        for item in raw_error_items
        if hasattr(item, "index") and hasattr(item, "error")
    }
    errors_by_url = cast(dict[str, list[str]], getattr(batch_result, "errors_by_url", {}))
    legacy_errors = getattr(batch_result, "errors", {})
    errors_by_url_offsets: dict[str, int] = {}
    _error_items_offsets: dict[str, int] = {}
    documents = list(getattr(batch_result, "documents", []))

    # Build a URL-keyed lookup from list-of-BatchErrorItem for the fallback path
    _error_items_by_url: dict[str, list[str]] = {}
    if isinstance(legacy_errors, list):
        for item in legacy_errors:
            if hasattr(item, "url") and hasattr(item, "error"):
                _error_items_by_url.setdefault(item.url, []).append(item.error)

    def fallback_error(index: int, url: str) -> str | None:
        if errors_by_url:
            options = errors_by_url.get(url) or []
            offset = errors_by_url_offsets.get(url, 0)
            if offset < len(options):
                errors_by_url_offsets[url] = offset + 1
                return options[offset]
            return None
        if isinstance(legacy_errors, dict):
            return cast(dict[str, str], legacy_errors).get(url)
        if _error_items_by_url:
            items = _error_items_by_url.get(url, [])
            offset = _error_items_offsets.get(url, 0)
            if offset < len(items):
                _error_items_offsets[url] = offset + 1
                return items[offset]
            return None
        return None

    rows = []
    for index, url in enumerate(urls):
        doc = documents[index] if index < len(documents) else None
        if index in error_by_index:
            error = error_by_index[index]
        else:
            error = fallback_error(index, url)
        if doc is None and error is None:
            error = f"Missing batch result for input index {index}"
        rows.append({"index": index, "url": url, "document": doc, "error": error})
    return rows


def create_batch_results_table(urls: list[str], batch_result) -> Table:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("URL", style="dim", width=40)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Score", justify="right", width=8)
    for row in _build_batch_rows(urls, batch_result):
        url_display = row["url"][:37] + "..." if len(row["url"]) > 40 else row["url"]
        doc = row["document"]
        if doc is not None:
            table.add_row(
                url_display, "[green]OK", str(doc.token_estimate), f"{doc.injection_score:.2f}"
            )
        else:
            table.add_row(url_display, "[red]X", "-", "-")
    return table


def save_batch_results(args, urls: list[str], batch_result) -> None:
    if not args.output:
        return
    output_path = Path(args.output)
    if args.json:
        rows = _build_batch_rows(urls, batch_result)
        successful = sum(1 for row in rows if row["document"] is not None)
        failed = sum(1 for row in rows if row["document"] is None)
        # Improved path detection logic
        if output_path.is_dir():
            # Existing directory - use default filename
            output_path = output_path / "results.json"
        elif output_path.suffix:
            # Has file extension - treat as file
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            # No extension: respect the exact path as the requested output file.
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_data = {
            "summary": {
                "total": len(rows),
                "successful": successful,
                "failed": failed,
                "success_rate": (successful / len(rows) * 100) if rows else 0.0,
            },
            "results": [
                (
                    batch_document_json_row(
                        row,
                        no_content=getattr(args, "no_content", False),
                    )
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
                for error_item in getattr(
                    batch_result, "error_items", getattr(batch_result, "errors", [])
                )
                if hasattr(error_item, "index")
                and hasattr(error_item, "url")
                and hasattr(error_item, "error")
            ],
            "errors_by_url": getattr(batch_result, "errors_by_url", {}),
            "error_items": [
                {
                    "index": error_item.index,
                    "url": error_item.url,
                    "error": error_item.error,
                }
                for error_item in getattr(
                    batch_result, "error_items", getattr(batch_result, "errors", [])
                )
                if hasattr(error_item, "index")
                and hasattr(error_item, "url")
                and hasattr(error_item, "error")
            ],
        }
        output_path.write_text(json.dumps(output_data, indent=2))
    else:
        output_path.mkdir(parents=True, exist_ok=True)
        if urls:
            rows = _build_batch_rows(urls, batch_result)
        else:
            rows = [
                {"index": i, "document": doc}
                for i, doc in enumerate(getattr(batch_result, "documents", []))
            ]
        for row in rows:
            doc = row["document"]
            if doc and not getattr(args, "no_content", False):
                (output_path / f"doc_{row['index'] + 1:03d}.md").write_text(doc.markdown)
    console.print(f"[green]Results saved to {output_path}")
