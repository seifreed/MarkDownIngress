"""JSON-LD helpers for metadata extraction."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

_MAX_JSONLD_ITEMS = 10_000


def iter_jsonld_items(data: Any) -> Iterator[dict[str, Any]]:
    """Yield JSON-LD object candidates from root lists and @graph containers."""
    stack: list[Any] = [data]
    seen_containers: set[int] = set()
    yielded = 0

    while stack and yielded < _MAX_JSONLD_ITEMS:
        current = stack.pop()
        if isinstance(current, (dict, list)):
            container_id = id(current)
            if container_id in seen_containers:
                continue
            seen_containers.add(container_id)

        if isinstance(current, dict):
            yield current
            yielded += 1
            graph = current.get("@graph")
            if isinstance(graph, list):
                stack.extend(reversed(graph))
            elif isinstance(graph, dict):
                stack.append(graph)
            continue

        if isinstance(current, list):
            stack.extend(reversed(current))


def _load_jsonld_script(script: Any) -> Any | None:
    try:
        return json.loads(script.text())
    except (json.JSONDecodeError, AttributeError, RecursionError):
        return None


def parse_author_from_jsonld_script(script: Any) -> str | None:
    """Parse author metadata from one JSON-LD script tag."""
    data = _load_jsonld_script(script)
    if data is None:
        return None

    for item in iter_jsonld_items(data):
        author = _parse_author_value(item.get("author"))
        if author:
            return author
    return None


def _parse_author_value(author: Any) -> str | None:
    if not author:
        return None
    if isinstance(author, list):
        return _parse_author_list(author)
    if isinstance(author, dict):
        return _parse_author_dict(author)
    if isinstance(author, str):
        return author
    return None


def _parse_author_list(author: list[Any]) -> str | None:
    names = []
    for author_item in author:
        if isinstance(author_item, dict):
            name = _parse_author_dict(author_item)
            if name:
                names.append(name)
        elif isinstance(author_item, str) and author_item:
            names.append(author_item)
    if names:
        return ", ".join(names)
    return None


def _parse_author_dict(author: dict[str, Any]) -> str | None:
    name = author.get("name")
    if isinstance(name, str) and name:
        return name
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
