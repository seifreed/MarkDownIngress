"""
Domain-specific DOM filtering rules.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from soupsieve import SelectorSyntaxError

from markdown_ingress.config_models import DomainPolicy

_logger = logging.getLogger(__name__)

_REMOVE_ENTIRELY_TAGS = {
    "head",
    "title",
    "meta",
    "link",
    "base",
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "applet",
    "template",
    "noscript",
}


def _empty_domain_filter_stats() -> dict:
    return {
        "blocked_tags_removed": 0,
        "blocked_selectors_removed": 0,
        "unwrapped_nodes": 0,
    }


def _remove_nodes_matching_selectors(
    soup: BeautifulSoup, selectors: list[str] | None, field_name: str
) -> int:
    removed = 0
    for selector in selectors or []:
        try:
            for node in soup.select(selector):
                node.decompose()
                removed += 1
        except (SelectorSyntaxError, NotImplementedError, ValueError):
            _logger.warning(
                "Invalid CSS selector in %s, skipping: %s",
                field_name,
                selector,
                exc_info=True,
            )
    return removed


def _unwrap_nodes_matching_selectors(soup: BeautifulSoup, selectors: list[str] | None) -> int:
    unwrapped = 0
    for selector in selectors or []:
        # Process innermost nodes first to avoid corrupting the tree when
        # nested elements match the same selector.
        try:
            for node in reversed(list(soup.select(selector))):
                if node.parent is not None:
                    node.unwrap()
                    unwrapped += 1
        except (SelectorSyntaxError, NotImplementedError, ValueError):
            _logger.warning(
                "Invalid CSS selector in unwrap_selectors, skipping: %s",
                selector,
                exc_info=True,
            )
    return unwrapped


def _remove_blocked_tags(soup: BeautifulSoup, tag_names: list[str] | None) -> int:
    removed = 0
    for tag_name in tag_names or []:
        for node in soup.find_all(tag_name):
            # Skip nodes already removed by blocked_selectors above;
            # decomposed nodes lose their parent reference.
            if node.parent is None:
                continue
            node.decompose()
            removed += 1
    return removed


def _apply_allowed_tags_filter(
    soup: BeautifulSoup, allowed_tags: list[str] | None
) -> tuple[int, int]:
    if not allowed_tags:
        return 0, 0
    allowed = {name.lower() for name in allowed_tags}
    unwrapped = 0
    decomposed = 0
    # Process innermost-first (reversed document order) so that
    # unwrapping a child before its parent keeps the tree consistent.
    for node in reversed(list(soup.find_all(True))):
        tag = node.name.lower()
        if tag in {"html", "body"} or tag in allowed:
            continue
        if tag in _REMOVE_ENTIRELY_TAGS:
            node.decompose()
            decomposed += 1
        else:
            node.unwrap()
            unwrapped += 1
    return unwrapped, decomposed


def apply_domain_html_rules(html: str, policy: DomainPolicy | None) -> tuple[str, dict]:
    """Apply granular domain-specific DOM filters to extracted HTML."""
    if policy is None:
        return html, _empty_domain_filter_stats()

    soup = BeautifulSoup(html, "html.parser")
    blocked_selectors_removed = _remove_nodes_matching_selectors(
        soup, policy.blocked_selectors, "blocked_selectors"
    )
    unwrapped_nodes = _unwrap_nodes_matching_selectors(soup, policy.unwrap_selectors)
    blocked_tags_removed = _remove_blocked_tags(soup, policy.blocked_tags)
    allowed_unwrapped_nodes, decomposed_nodes = _apply_allowed_tags_filter(
        soup, policy.allowed_tags
    )
    unwrapped_nodes += allowed_unwrapped_nodes

    return str(soup), {
        "blocked_tags_removed": blocked_tags_removed,
        "blocked_selectors_removed": blocked_selectors_removed,
        "unwrapped_nodes": unwrapped_nodes,
        "decomposed_nodes": decomposed_nodes,
    }
