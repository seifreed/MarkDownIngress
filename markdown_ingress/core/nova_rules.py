"""Rule loading helpers for Nova prompt-injection detection."""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

MAX_RULES_FILE_SIZE_BYTES = 10 * 1024 * 1024

RAW_ENCODED_TRAVERSAL_PATTERNS = (
    "%2e%2e",
    "%252e",
    "%5c",
    "%c0%ae",
    "%c1%9c",
    "%c0%af",
    "%c0%2f",
    "..%00",
    "..%252f",
    "..%255c",
    "%e0%80%ae",
    "..%c0%ae/",
)


class RuleParser(Protocol):
    """Parser contract used by the optional Nova dependency."""

    def parse(self, content: str) -> Any: ...


@dataclass(frozen=True)
class RuleParseFailure:
    """A bundled rule block that failed to parse."""

    preview: str
    error: str


def _nova_parser_error_type() -> type[Exception]:
    try:
        parser_module = import_module("nova.core.parser")
    except ImportError:
        return ValueError
    parser_error = getattr(parser_module, "NovaParserError", ValueError)
    if isinstance(parser_error, type) and issubclass(parser_error, Exception):
        return parser_error
    return ValueError


RULE_PARSE_ERRORS: tuple[type[Exception], ...] = (
    AttributeError,
    LookupError,
    _nova_parser_error_type(),
    RuntimeError,
    SyntaxError,
    TypeError,
    ValueError,
)


def reject_unsafe_rules_path(rules_path: str) -> None:
    """Reject null-byte and traversal patterns before resolving a rules path."""
    if "\x00" in str(rules_path) or "%00" in str(rules_path).lower():
        raise ValueError(f"Null bytes not allowed in rules_path: {rules_path}")

    raw_lower = str(rules_path).lower().replace("\\", "/")
    fully_decoded = _decode_fully(str(rules_path))
    normalized_lower = fully_decoded.lower().replace("\\", "/")

    if any(pattern in raw_lower for pattern in RAW_ENCODED_TRAVERSAL_PATTERNS):
        raise ValueError(f"Path traversal not allowed in rules_path: {rules_path}")
    if ".." in normalized_lower or "..;/" in normalized_lower:
        raise ValueError(f"Path traversal not allowed in rules_path: {rules_path}")


def _decode_fully(encoded_path: str) -> str:
    """Decode URL-encoded text until fixed point with a defensive cap."""
    decoded = encoded_path
    for _ in range(25):
        new_decoded = urllib.parse.unquote(decoded)
        if new_decoded == decoded:
            break
        decoded = new_decoded
    return decoded


def read_rules_file_atomically(
    resolved_path: Path,
    *,
    max_size: int = MAX_RULES_FILE_SIZE_BYTES,
) -> str:
    """Open and read a rules file after checking the open descriptor size."""
    with resolved_path.open("r", encoding="utf-8") as f:
        file_size = os.fstat(f.fileno()).st_size
        if file_size > max_size:
            raise ValueError(
                f"Rules file too large: {file_size} bytes "
                f"(max {max_size} bytes). "
                "Large files may indicate a DoS attempt."
            )
        return f.read()


def parse_rule_content(content: str, parser: RuleParser) -> list[Any]:
    """Parse either multiple top-level rule blocks or a whole rules document."""
    blocks = extract_rule_blocks(content)
    if not blocks:
        return [parser.parse(content)]
    return [parser.parse(block.strip()) for block in blocks]


def parse_bundled_rule_content(
    content: str,
    parser: RuleParser,
) -> tuple[list[Any], list[RuleParseFailure]]:
    """Parse bundled rule blocks while preserving per-block failures."""
    rules: list[Any] = []
    failures: list[RuleParseFailure] = []
    for block in extract_rule_blocks(content):
        try:
            rules.append(parser.parse(block.strip()))
        except RULE_PARSE_ERRORS as exc:
            failures.append(RuleParseFailure(block[:50], str(exc)))
    return rules, failures


def extract_rule_blocks(content: str) -> list[str]:
    """Extract top-level ``rule <name> { ... }`` blocks in O(n) time."""
    blocks: list[str] = []
    for match in re.finditer(r"rule\s+\w+\s*\{", content):
        start = match.start()
        depth = 1
        pos = match.end()
        while pos < len(content) and depth > 0:
            ch = content[pos]
            if ch == '"' or ch == "'":
                quote = ch
                pos += 1
                while pos < len(content) and content[pos] != quote:
                    if content[pos] == "\\":
                        pos += 1
                        if pos >= len(content):
                            break
                    pos += 1
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        if depth == 0:
            blocks.append(content[start:pos])
    return blocks
