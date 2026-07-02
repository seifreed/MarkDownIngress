"""Metadata helpers for structured HTML blocks."""

from __future__ import annotations

from bs4 import Tag

CODE_LANGUAGE_CLASS_PREFIXES = ("language-", "lang-", "highlight-")


def detect_code_language(element: Tag) -> str | None:
    raw_classes: list[str] = []
    for candidate in (element, element.find("code")):
        if candidate is None:
            continue
        classes = candidate.get("class")
        if isinstance(classes, str):
            raw_classes.append(classes)
        else:
            raw_classes.extend(list(classes or []))

    for value in raw_classes:
        for prefix in CODE_LANGUAGE_CLASS_PREFIXES:
            if value.startswith(prefix):
                return value.removeprefix(prefix)
    return None


def build_block_metadata(element: Tag, block_type: str) -> dict:
    metadata: dict[str, object] = {"tag": element.name}
    if block_type == "table":
        rows = element.find_all("tr")
        metadata["rows"] = len(rows)
        metadata["columns"] = max(
            (len(row.find_all(["th", "td"])) for row in rows),
            default=0,
        )
    if block_type == "code":
        metadata["language"] = detect_code_language(element)
    return metadata
