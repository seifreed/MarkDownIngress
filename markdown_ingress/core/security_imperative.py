"""Imperative-language metrics for prompt-injection analysis."""

from __future__ import annotations

import re
from collections.abc import Collection

from markdown_ingress.core.security_text import _normalize_security_text


def calculate_imperative_density(text: str, imperative_verbs: Collection[str]) -> float:
    """Return the ratio of imperative verbs to total words."""
    normalized_text = _normalize_security_text(text.lower())
    words = re.findall(r"\b\w+\b", normalized_text)

    if not words:
        return 0.0

    imperative_count = sum(1 for word in words if word in imperative_verbs)
    return imperative_count / len(words)
