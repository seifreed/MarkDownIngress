"""Basic HTML metadata extraction helpers."""

from __future__ import annotations

from selectolax.parser import HTMLParser

_CONTENT_TYPE_SELECTORS = (
    ("article", ("article", 'meta[property="og:type"][content="article"]', ".post", ".article")),
    (
        "documentation",
        (".documentation", "#docs", ".docs", ".api-doc", ".api-docs", ".api-documentation"),
    ),
    (
        "forum",
        (
            ".forum",
            ".discussion",
            ".thread",
            ".comment",
            ".comments",
            ".comment-list",
            ".comment-thread",
        ),
    ),
    ("ecommerce", (".product", '[itemtype*="Product"]', ".price", ".add-to-cart")),
)


def first_meta_content(parser: HTMLParser, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        node = parser.css_first(selector)
        if node:
            content = (node.attributes.get("content") or "").strip()
            if content:
                return content
    return None


def extract_keywords(parser: HTMLParser) -> str | None:
    keywords = first_meta_content(parser, ('meta[name="keywords"]',))
    if keywords:
        return keywords

    tags = [
        (tag.attributes.get("content") or "").strip()
        for tag in parser.css('meta[property="article:tag"]')
    ]
    tags = [tag for tag in tags if tag]
    return ", ".join(tags) if tags else None


def detect_content_type(parser: HTMLParser) -> str:
    for content_type, selectors in _CONTENT_TYPE_SELECTORS:
        if any(parser.css_first(selector) for selector in selectors):
            return content_type
    return "webpage"
