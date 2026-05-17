"""Batch display and file-output helpers for the CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from rich.table import Table

from markdown_ingress.cli_console import console
from markdown_ingress.cli_json_output import batch_document_json_row


@dataclass
class _BatchErrorLookup:
    by_index: dict[int, str]
    by_url: dict[str, list[str]]
    legacy_errors: Any
    legacy_items_by_url: dict[str, list[str]]
    by_url_offsets: dict[str, int] = field(default_factory=dict)
    legacy_item_offsets: dict[str, int] = field(default_factory=dict)

    def error_for(self, index: int, url: str) -> str | None:
        if index in self.by_index:
            return self.by_index[index]
        return self._fallback_error(url)

    def _fallback_error(self, url: str) -> str | None:
        if self.by_url:
            return _next_offset_error(self.by_url, self.by_url_offsets, url)
        if isinstance(self.legacy_errors, dict):
            return cast(dict[str, str], self.legacy_errors).get(url)
        if self.legacy_items_by_url:
            return _next_offset_error(self.legacy_items_by_url, self.legacy_item_offsets, url)
        return None


def _indexed_errors(raw_error_items: Any) -> dict[int, str]:
    return {
        item.index: item.error
        for item in raw_error_items
        if hasattr(item, "index") and hasattr(item, "error")
    }


def _legacy_error_items_by_url(legacy_errors: Any) -> dict[str, list[str]]:
    items_by_url: dict[str, list[str]] = {}
    if not isinstance(legacy_errors, list):
        return items_by_url
    for item in legacy_errors:
        if hasattr(item, "url") and hasattr(item, "error"):
            items_by_url.setdefault(item.url, []).append(item.error)
    return items_by_url


def _next_offset_error(
    errors_by_url: dict[str, list[str]],
    offsets: dict[str, int],
    url: str,
) -> str | None:
    options = errors_by_url.get(url) or []
    offset = offsets.get(url, 0)
    if offset >= len(options):
        return None
    offsets[url] = offset + 1
    return options[offset]


def _batch_error_lookup(batch_result: Any) -> _BatchErrorLookup:
    raw_error_items = getattr(batch_result, "error_items", getattr(batch_result, "errors", []))
    legacy_errors = getattr(batch_result, "errors", {})
    return _BatchErrorLookup(
        by_index=_indexed_errors(raw_error_items),
        by_url=cast(dict[str, list[str]], getattr(batch_result, "errors_by_url", {})),
        legacy_errors=legacy_errors,
        legacy_items_by_url=_legacy_error_items_by_url(legacy_errors),
    )


def _batch_error_item_json(error_item: Any) -> dict[str, Any]:
    payload = {
        "index": error_item.index,
        "url": error_item.url,
        "error": error_item.error,
    }
    error_type = getattr(error_item, "error_type", "")
    if error_type:
        payload["error_type"] = error_type
    traceback_text = getattr(error_item, "traceback", "")
    if traceback_text:
        payload["traceback"] = traceback_text
    return payload


def load_urls_from_file(filepath: str) -> list[str]:
    urls_file = Path(filepath)
    if not urls_file.exists():
        console.print(f"[red]Error: File not found: {filepath}")
        raise SystemExit(1)
    urls = [
        line.strip()
        for line in urls_file.read_text(encoding="utf-8").splitlines()
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
    error_lookup = _batch_error_lookup(batch_result)
    documents = list(getattr(batch_result, "documents", []))

    rows = []
    for index, url in enumerate(urls):
        doc = documents[index] if index < len(documents) else None
        error = error_lookup.error_for(index, url)
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
        if output_path.is_dir():
            output_path = output_path / "results.json"
        elif output_path.suffix:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
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
                _batch_error_item_json(error_item)
                for error_item in getattr(
                    batch_result, "error_items", getattr(batch_result, "errors", [])
                )
                if hasattr(error_item, "index")
                and hasattr(error_item, "url")
                and hasattr(error_item, "error")
            ],
            "errors_by_url": getattr(batch_result, "errors_by_url", {}),
            "error_items": [
                _batch_error_item_json(error_item)
                for error_item in getattr(
                    batch_result, "error_items", getattr(batch_result, "errors", [])
                )
                if hasattr(error_item, "index")
                and hasattr(error_item, "url")
                and hasattr(error_item, "error")
            ],
        }
        output_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
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
                (output_path / f"doc_{row['index'] + 1:03d}.md").write_text(
                    doc.markdown,
                    encoding="utf-8",
                )
    console.print(f"[green]Results saved to {output_path}")
