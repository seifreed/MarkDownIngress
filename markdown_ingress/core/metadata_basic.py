"""Basic HTML metadata extraction helpers."""

from __future__ import annotations

from selectolax.parser import HTMLParser


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
    if (
        parser.css_first("article")
        or parser.css_first('meta[property="og:type"][content="article"]')
        or parser.css_first(".post")
        or parser.css_first(".article")
    ):
        return "article"

    if (
        parser.css_first(".documentation")
        or parser.css_first("#docs")
        or parser.css_first(".docs")
        or parser.css_first(".api-doc")
        or parser.css_first(".api-docs")
        or parser.css_first(".api-documentation")
    ):
        return "documentation"

    if (
        parser.css_first(".forum")
        or parser.css_first(".discussion")
        or parser.css_first(".thread")
        or parser.css_first(".comment")
        or parser.css_first(".comments")
        or parser.css_first(".comment-list")
        or parser.css_first(".comment-thread")
    ):
        return "forum"

    if (
        parser.css_first(".product")
        or parser.css_first('[itemtype*="Product"]')
        or parser.css_first(".price")
        or parser.css_first(".add-to-cart")
    ):
        return "ecommerce"

    return "webpage"
