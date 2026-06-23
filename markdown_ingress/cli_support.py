"""Display and file helpers for the CLI."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from markdown_ingress.cli_batch_output import (
    _build_batch_rows,
    _iter_batch_error_items,
    create_batch_results_table,
    display_batch_summary,
    load_urls_from_file,
    save_batch_results,
)
from markdown_ingress.cli_console import console
from markdown_ingress.cli_json_output import (
    batch_document_json_row,
    document_json_fields,
)

__all__ = [
    "_build_batch_rows",
    "_iter_batch_error_items",
    "batch_document_json_row",
    "console",
    "create_batch_results_table",
    "display_batch_summary",
    "display_rich_output",
    "document_json_fields",
    "load_domain_policies",
    "load_urls_from_file",
    "save_batch_results",
    "save_json_output",
    "save_markdown_output",
]


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
        Path(args.save).write_text(output, encoding="utf-8")
        console.print(f"[green]JSON saved to {args.save}")
    else:
        print(output)


def save_markdown_output(doc, args) -> None:
    if args.save:
        Path(args.save).write_text(doc.markdown, encoding="utf-8")
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
        policies.extend(_load_domain_policy_file(policy_file))
    policies.extend(_load_inline_domain_policies(inline_policies))
    return policies


def _load_domain_policy_file(policy_file):
    loaded = _read_domain_policy_json_file(policy_file)
    return _normalize_domain_policy_file_payload(loaded)


def _read_domain_policy_json_file(policy_file):
    try:
        content = Path(policy_file).read_text(encoding="utf-8")
        return json.loads(content)
    except OSError as exc:
        console.print(f"[red]Error: Could not read domain policy file {policy_file}: {exc}")
        raise SystemExit(1) from None
    except json.JSONDecodeError as exc:
        console.print(f"[red]Error: Invalid JSON in domain policy file {policy_file}: {exc}")
        raise SystemExit(1) from None


def _normalize_domain_policy_file_payload(loaded):
    if isinstance(loaded, dict):
        return [loaded]
    if isinstance(loaded, list):
        return _normalize_domain_policy_file_array(loaded)
    console.print("[red]Error: Domain policy file must contain a JSON object or array")
    raise SystemExit(1)


def _normalize_domain_policy_file_array(loaded):
    policies = []
    for index, item in enumerate(loaded):
        if not isinstance(item, Mapping):
            console.print(
                "[red]Error: Domain policy file array entries must be JSON objects "
                f"(invalid entry at index {index}: {type(item).__name__})"
            )
            raise SystemExit(1)
        policies.append(dict(item))
    return policies


def _load_inline_domain_policies(inline_policies):
    return [_load_inline_domain_policy(inline) for inline in inline_policies]


def _load_inline_domain_policy(inline):
    try:
        loaded_inline = json.loads(inline)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Error: Invalid JSON in --domain-policy: {exc}")
        raise SystemExit(1) from None
    if not isinstance(loaded_inline, Mapping):
        console.print("[red]Error: --domain-policy must decode to a JSON object")
        raise SystemExit(1)
    return dict(loaded_inline)
