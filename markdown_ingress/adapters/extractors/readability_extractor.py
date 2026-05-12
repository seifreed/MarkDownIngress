"""HTML extraction adapter using readability-lxml and selectolax."""

import logging
import re

from readability import Document  # type: ignore[import-untyped]
from selectolax.parser import HTMLParser

from markdown_ingress.core.interfaces import IExtractor
from markdown_ingress.models import ExtractionResult

logger = logging.getLogger(__name__)


def _is_empty_body(html: str) -> bool:
    """Detect an empty/whitespace-only body element (case-insensitive)."""
    return bool(re.search(r"<\s*body\s*>\s*<\s*/\s*body\s*>", html, re.IGNORECASE))


_DANGEROUS_DATA_URL_PREFIXES = (
    "text/html",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/ecmascript",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "image/svg+xml",
    "image/svg",
)

_SAFE_DATA_URL_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")


class Extractor(IExtractor):
    """Extract main content and remove unnecessary elements."""

    REMOVE_TAGS = (
        "script",
        "style",
        "nav",
        "footer",
        "aside",
        "iframe",
        "noscript",
        "object",
        "embed",
        "applet",
        "base",
    )

    def __init__(self, strict: bool = True):
        self.strict = strict

    @staticmethod
    def _sanitize_html(html: str) -> str:
        """Drop control characters that break downstream HTML/XML tooling."""
        return _CONTROL_CHARS_RE.sub("", html)

    def extract(self, html: str, url: str) -> ExtractionResult:
        if not html or not html.strip():
            raise ValueError(f"Empty or whitespace-only HTML provided for URL: {url}")

        sanitized_html = self._sanitize_html(html)

        tree_pre = HTMLParser(sanitized_html)
        removed_hidden = self._remove_hidden_elements(tree_pre)
        pre_cleaned_html = tree_pre.html or sanitized_html

        try:
            doc = Document(pre_cleaned_html)
            title = doc.title()
            content_html = doc.summary(html_partial=False)
        except Exception as e:
            logger.warning(
                "Readability extraction failed for URL %s, falling back to raw content: %s",
                url,
                e,
            )
            title = None
            content_html = pre_cleaned_html

        if len(content_html.strip()) < 50 or _is_empty_body(content_html):
            tree_fallback = HTMLParser(pre_cleaned_html)
            body = tree_fallback.css_first("body")
            if body:
                content_html = f"<html><body>{body.html or ''}</body></html>"
            else:
                content_html = pre_cleaned_html  # pragma: no cover

        tree = HTMLParser(content_html)
        removed_tags = {}

        for tag_name in self.REMOVE_TAGS:
            elements = tree.css(tag_name)
            count = len(elements)
            if count > 0:
                removed_tags[tag_name] = count
                for elem in elements:
                    elem.decompose()

        sanitize_stats = self._sanitize_dangerous_content(tree)
        total_removed = sum(sanitize_stats.values())
        if total_removed > 0:
            logger.debug(
                "Sanitized %d dangerous items from %s: %d event handlers, %d javascript URLs, "
                "%d style attributes, %d data URLs, %d vbscript URLs",
                total_removed,
                url,
                sanitize_stats["event_handlers"],
                sanitize_stats["javascript_urls"],
                sanitize_stats["style_attributes"],
                sanitize_stats["data_urls"],
                sanitize_stats["vbscript_urls"],
            )

        cleaned_html = tree.html or ""
        text_content = tree.text(separator=" ", strip=True) or ""

        return ExtractionResult(
            html=cleaned_html,
            title=title,
            author=None,
            removed_tags=removed_tags,
            removed_hidden=removed_hidden,
            text_content=text_content,
        )

    def _remove_hidden_elements(self, tree: HTMLParser) -> int:
        count = 0

        hidden_selectors = [
            "[hidden]",
            '[aria-hidden="true"]',
            '[style*="left:-999"]',
            '[style*="left: -999"]',
            '[style*="top:-999"]',
            '[style*="top: -999"]',
            '[style*="clip:rect(0"]',
            '[style*="clip: rect(0"]',
            '[style*="clip-path:inset(100%)"]',
            '[style*="clip-path: inset(100%)"]',
            '[style*="Clip-Path:inset(100%)"]',
            '[style*="transform:scale(0)"]',
            '[style*="transform: scale(0)"]',
            '[style*="transform:translate(-999"]',
            '[style*="transform: translate(-999"]',
            '[style*="Transform:Scale(0)"]',
            '[style*="content-visibility:hidden"]',
            '[style*="content-visibility: hidden"]',
            '[style*="Content-Visibility:hidden"]',
            '[style*="color:transparent"]',
            '[style*="color: transparent"]',
            '[style*="Color:Transparent"]',
            ".hidden",
            ".invisible",
            ".hide",
            ".sr-only",
            ".visually-hidden",
        ]

        decomposed_ids: set[int] = set()
        for selector in hidden_selectors:
            try:
                elements = tree.css(selector)
                for elem in elements:
                    eid = id(elem)
                    if eid not in decomposed_ids:
                        decomposed_ids.add(eid)
                        count += 1
                        elem.decompose()
            except (AttributeError, ValueError, TypeError) as e:
                logger.debug("Selector '%s' not supported: %s", selector, e)
                continue
            except Exception as e:
                logger.warning("Unexpected error processing selector '%s': %s", selector, e)
                continue

        def _style_has_exact_zero(style_value: str, property_name: str) -> bool:
            for declaration in style_value.split(";"):
                if ":" not in declaration:
                    continue
                name, value = declaration.split(":", 1)
                if name.strip().lower() != property_name:
                    continue
                normalized_value = value.strip().lower()
                if normalized_value.endswith("!important"):
                    normalized_value = normalized_value[: -len("!important")].strip()
                match = re.match(
                    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:[a-z%]*)$",
                    normalized_value,
                )
                if match is None:
                    continue
                try:
                    return float(match.group(1)) == 0.0
                except ValueError:
                    continue
            return False

        def _style_has_exact_keyword(
            style_value: str, property_name: str, expected_values: set[str]
        ) -> bool:
            for declaration in style_value.split(";"):
                if ":" not in declaration:
                    continue
                name, value = declaration.split(":", 1)
                if name.strip().lower() != property_name:
                    continue
                normalized_value = re.sub(r"\s+", "", value.strip().lower())
                normalized_value = re.sub(r"/\*.*?\*/", "", normalized_value)
                if normalized_value.endswith("!important"):
                    normalized_value = normalized_value[: -len("!important")]
                if normalized_value in expected_values:
                    return True
            return False

        def _style_has_prefix_value(
            style_value: str, property_name: str, prefixes: tuple[str, ...]
        ) -> bool:
            for declaration in style_value.split(";"):
                if ":" not in declaration:
                    continue
                name, value = declaration.split(":", 1)
                if name.strip().lower() != property_name:
                    continue
                normalized_value = re.sub(r"\s+", "", value.strip().lower())
                if normalized_value.endswith("!important"):
                    normalized_value = normalized_value[: -len("!important")]
                if any(normalized_value.startswith(prefix) for prefix in prefixes):
                    return True
            return False

        def _style_has_negative_offset(
            style_value: str, property_name: str, threshold: float = -999.0
        ) -> bool:
            for declaration in style_value.split(";"):
                if ":" not in declaration:
                    continue
                name, value = declaration.split(":", 1)
                if name.strip().lower() != property_name:
                    continue
                normalized_value = re.sub(r"\s+", "", value.strip().lower())
                if normalized_value.endswith("!important"):
                    normalized_value = normalized_value[: -len("!important")]
                match = re.match(
                    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:[a-z%]*)$",
                    normalized_value,
                )
                if match is None:
                    continue
                try:
                    if float(match.group(1)) <= threshold:
                        return True
                except ValueError:
                    continue
            return False

        for node in list(tree.css("*")):
            if not hasattr(node, "attributes") or not node.attributes:
                continue
            if node.parent is None:
                continue
            style_value = node.attributes.get("style")
            if not style_value:
                continue
            normalized_style = str(style_value).lower()
            if (
                _style_has_exact_zero(normalized_style, "opacity")
                or _style_has_exact_zero(normalized_style, "height")
                or _style_has_exact_zero(normalized_style, "width")
                or _style_has_exact_zero(normalized_style, "font-size")
                or _style_has_exact_keyword(normalized_style, "display", {"none"})
                or _style_has_exact_keyword(normalized_style, "visibility", {"hidden", "collapse"})
                or _style_has_exact_keyword(normalized_style, "content-visibility", {"hidden"})
                or _style_has_exact_keyword(normalized_style, "color", {"transparent"})
                or _style_has_exact_keyword(normalized_style, "clip-path", {"inset(100%)"})
                or _style_has_prefix_value(normalized_style, "clip", ("rect(0",))
                or _style_has_prefix_value(
                    normalized_style, "transform", ("scale(0)", "translate(-999")
                )
                or _style_has_negative_offset(normalized_style, "left")
                or _style_has_negative_offset(normalized_style, "top")
            ):
                node.decompose()
                count += 1

        return count

    def _sanitize_dangerous_content(self, tree: HTMLParser) -> dict[str, int]:
        result = {
            "event_handlers": 0,
            "style_attributes": 0,
            "javascript_urls": 0,
            "data_urls": 0,
            "vbscript_urls": 0,
        }

        url_attributes = {
            "href",
            "src",
            "action",
            "formaction",
            "xlink:href",
            "poster",
            "data",
            "code",
            "codebase",
        }

        for node in tree.css("*"):
            if not hasattr(node, "attributes") or not node.attributes:
                continue

            attrs_to_remove = []

            for attr_name in list(node.attributes.keys()):
                attr_value = node.attributes.get(attr_name)
                if not attr_value:
                    continue

                attr_name_lower = attr_name.lower()

                if attr_name_lower in {"srcset", "imagesrcset"}:
                    attrs_to_remove.append(attr_name)
                    if attr_value:
                        for part in str(attr_value).split(","):
                            if part.strip().startswith("data:"):
                                result["data_urls"] += 1
                                break
                    continue

                if attr_name_lower.startswith("on"):
                    attrs_to_remove.append(attr_name)
                    result["event_handlers"] += 1
                    continue

                if attr_name_lower == "style":
                    attrs_to_remove.append(attr_name)
                    result["style_attributes"] += 1
                    continue

                if attr_name_lower in url_attributes:
                    clean_value = str(attr_value).strip().lower()
                    clean_value = "".join(
                        c for c in clean_value if c not in "\x00\x1a\t\n\r\x0b\x0c"
                    )

                    if clean_value.startswith("javascript:"):
                        attrs_to_remove.append(attr_name)
                        result["javascript_urls"] += 1
                    elif clean_value.startswith("vbscript:"):
                        attrs_to_remove.append(attr_name)
                        result["vbscript_urls"] += 1
                    elif clean_value.startswith("data:"):
                        data_content = clean_value[5:]
                        comma_pos = data_content.find(",")
                        media_type_part = (
                            data_content[:comma_pos].split(";")[0].strip()
                            if comma_pos != -1
                            else ""
                        )
                        if not media_type_part:
                            # RFC 2397: data:,hello  -> media type is text/plain by default
                            media_type_part = "text/plain"
                        if media_type_part not in _SAFE_DATA_URL_MEDIA_TYPES:
                            attrs_to_remove.append(attr_name)
                            result["data_urls"] += 1

            for attr_name in attrs_to_remove:
                try:
                    del node.attrs[attr_name]
                except Exception as exc:
                    logger.warning(
                        "Failed to delete dangerous attribute %s on <%s>: %s",
                        attr_name,
                        node.tag,
                        exc,
                    )
                    try:
                        node.attrs[attr_name] = ""
                    except Exception:
                        logger.warning(
                            "Unable to neutralize dangerous attribute %s on <%s>",
                            attr_name,
                            node.tag,
                        )

        return result
