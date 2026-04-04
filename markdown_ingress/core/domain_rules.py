"""
Domain-specific DOM filtering rules.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from markdown_ingress.config_models import DomainPolicy


def apply_domain_html_rules(html: str, policy: DomainPolicy | None) -> tuple[str, dict]:
    """Apply granular domain-specific DOM filters to extracted HTML."""
    if policy is None:
        return html, {"blocked_tags_removed": 0, "blocked_selectors_removed": 0, "unwrapped_nodes": 0}

    soup = BeautifulSoup(html, "html.parser")
    blocked_tags_removed = 0
    blocked_selectors_removed = 0
    unwrapped_nodes = 0

    for selector in policy.blocked_selectors:
        for node in soup.select(selector):
            node.decompose()
            blocked_selectors_removed += 1

    for selector in policy.unwrap_selectors:
        # Process innermost nodes first to avoid corrupting the tree when
        # nested elements match the same selector.
        for node in reversed(soup.select(selector)):
            node.unwrap()
            unwrapped_nodes += 1

    for tag_name in policy.blocked_tags:
        for node in soup.find_all(tag_name):
            # Skip nodes already removed by blocked_selectors above;
            # decomposed nodes lose their parent reference.
            if node.parent is None:
                continue
            node.decompose()
            blocked_tags_removed += 1

    if policy.allowed_tags:
        allowed = {name.lower() for name in policy.allowed_tags}
        # Process innermost-first (reversed document order) so that
        # unwrapping a child before its parent keeps the tree consistent.
        for node in reversed(list(soup.find_all(True))):
            tag = node.name.lower()
            if tag in {"html", "body"} or tag in allowed:
                continue
            node.unwrap()
            unwrapped_nodes += 1

    return str(soup), {
        "blocked_tags_removed": blocked_tags_removed,
        "blocked_selectors_removed": blocked_selectors_removed,
        "unwrapped_nodes": unwrapped_nodes,
    }
