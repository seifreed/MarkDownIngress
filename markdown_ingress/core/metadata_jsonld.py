"""JSON-LD helpers for metadata extraction."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def iter_jsonld_items(data: Any) -> Iterator[dict[str, Any]]:
    """Yield JSON-LD object candidates from root lists and @graph containers."""
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from iter_jsonld_items(item)
        elif isinstance(graph, dict):
            yield from iter_jsonld_items(graph)
        return
    if isinstance(data, list):
        for item in data:
            yield from iter_jsonld_items(item)


def _load_jsonld_script(script: Any) -> Any | None:
    try:
        return json.loads(script.text())
    except (json.JSONDecodeError, AttributeError):
        return None


def parse_author_from_jsonld_script(script: Any) -> str | None:
    """Parse author metadata from one JSON-LD script tag."""
    data = _load_jsonld_script(script)
    if data is None:
        return None

    for item in iter_jsonld_items(data):
        author = item.get("author")
        if not author:
            continue

        if isinstance(author, list):
            names = []
            for author_item in author:
                if isinstance(author_item, dict):
                    name = author_item.get("name")
                    if isinstance(name, str) and name:
                        names.append(name)
                elif isinstance(author_item, str) and author_item:
                    names.append(author_item)
            if names:
                return ", ".join(names)
            continue
        if isinstance(author, dict):
            name = author.get("name")
            if isinstance(name, str) and name:
                return name
            continue
        if isinstance(author, str):
            return author
    return None


def parse_date_from_jsonld_script(script: Any, date_field: str) -> str | None:
    """Parse a date field from one JSON-LD script tag."""
    data = _load_jsonld_script(script)
    if data is None:
        return None

    for item in iter_jsonld_items(data):
        date_value = item.get(date_field)
        if isinstance(date_value, str) and date_value:
            return date_value
    return None
