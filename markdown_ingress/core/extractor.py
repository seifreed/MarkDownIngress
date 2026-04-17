"""
HTML extraction and cleaning module
"""

import logging
import re

from readability import Document  # type: ignore[import-untyped]
from selectolax.parser import HTMLParser

from markdown_ingress.models import ExtractionResult

logger = logging.getLogger(__name__)

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

# Only these image media types are safe in data: URLs; everything else is blocked.
_SAFE_DATA_URL_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")


class Extractor:  # implements IExtractor protocol
    """Extract main content and remove unnecessary elements"""

    # Security-focused list of tags to remove completely
    # Includes XSS-dangerous elements: script, style, iframe, object, embed, applet, base
    REMOVE_TAGS = [
        "script",
        "style",
        "nav",
        "footer",
        "aside",
        "iframe",
        "noscript",
        "object",  # Security: prevents object-based XSS
        "embed",  # Security: prevents embed-based XSS
        "applet",  # Security: prevents Java applet attacks
        "base",  # Security: prevents base URL hijacking
    ]

    def __init__(self, strict: bool = True):
        self.strict = strict

    @staticmethod
    def _sanitize_html(html: str) -> str:
        """Drop control characters that break downstream HTML/XML tooling.

        Removes C0 control characters (U+0000–U+001F except tab/newline/CR)
        and C1 control characters (U+0080–U+009F) which are HTML5 parse errors.
        """
        return _CONTROL_CHARS_RE.sub("", html)

    def extract(self, html: str, url: str) -> ExtractionResult:
        """
        Extract main content using readability and clean DOM.
        Falls back to full-page extraction if readability returns empty content.

        Args:
            html: Raw HTML content
            url: Source URL (for readability context)

        Returns:
            ExtractionResult with cleaned HTML and metadata

        Raises:
            ValueError: If html is empty or contains only whitespace
        """
        # Validate input - empty HTML should be rejected early
        if not html or not html.strip():
            raise ValueError(f"Empty or whitespace-only HTML provided for URL: {url}")

        sanitized_html = self._sanitize_html(html)

        # First, parse the raw HTML to remove hidden elements BEFORE readability
        # Note: removed_hidden counts elements removed at this pre-readability stage.
        # Readability may have already excluded some hidden elements internally,
        # so this count reflects only explicit removals from the pre-parsed tree.
        tree_pre = HTMLParser(sanitized_html)
        removed_hidden = self._remove_hidden_elements(tree_pre)
        pre_cleaned_html = tree_pre.html or sanitized_html

        # Apply readability extraction on pre-cleaned HTML
        try:
            doc = Document(pre_cleaned_html)
            title = doc.title()
            content_html = doc.summary(html_partial=False)
        except Exception as e:
            # readability-lxml can raise ValueError, IndexError, AttributeError,
            # or RuntimeError on malformed HTML — fall back to raw content.
            logger.warning(
                "Readability extraction failed for URL %s, falling back to raw content: %s",
                url,
                e,
            )
            title = None
            content_html = pre_cleaned_html

        # Check if readability returned meaningful content
        # If the output is essentially empty (just <html><body></body></html>)
        # fall back to using the body of the pre-cleaned HTML
        if len(content_html.strip()) < 50 or "<body></body>" in content_html:
            # Fallback: use body from pre-cleaned HTML
            tree_fallback = HTMLParser(pre_cleaned_html)
            body = tree_fallback.css_first("body")
            if body:
                content_html = f"<html><body>{body.html or ''}</body></html>"
            else:
                # Ultimate fallback: use entire pre-cleaned HTML
                content_html = pre_cleaned_html  # pragma: no cover

        # Parse with selectolax for fast manipulation
        # Note: content_html already comes from sanitized input, so no need to re-sanitize
        tree = HTMLParser(content_html)

        # Track removed elements
        removed_tags = {}

        # Remove unwanted tags
        for tag_name in self.REMOVE_TAGS:
            elements = tree.css(tag_name)
            count = len(elements)
            if count > 0:
                removed_tags[tag_name] = count
                for elem in elements:
                    elem.decompose()

        # Security: Sanitize dangerous content (event handlers, javascript URLs, etc.)
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

        # Get cleaned HTML
        cleaned_html = tree.html or ""

        # Extract text content
        text_content = tree.text(separator=" ", strip=True) or ""

        return ExtractionResult(
            html=cleaned_html,
            title=title,
            author=None,  # readability-lxml doesn't expose author in basic usage
            removed_tags=removed_tags,
            removed_hidden=removed_hidden,
            text_content=text_content,
        )

    def _remove_hidden_elements(self, tree: HTMLParser) -> int:
        """
        Remove elements that are hidden via CSS or attributes.

        Returns count of removed elements.
        """
        count = 0

        # CSS selectors for hidden elements
        # Note: Case-insensitive matching for style attributes
        hidden_selectors = [
            # Attribute-based hiding
            "[hidden]",
            '[aria-hidden="true"]',
            # NOTE: display/visibility inline-style hiding is handled by the
            # second pass below using _style_has_exact_keyword(), which avoids
            # false-positive substring matches (e.g. "display:nonexistent").
            # Opacity-based hiding
            # Size-based hiding (height/width/font-size = 0)
            # Position-based hiding (off-screen)
            '[style*="left:-999"]',
            '[style*="left: -999"]',
            '[style*="top:-999"]',
            '[style*="top: -999"]',
            # Clip-based hiding
            '[style*="clip:rect(0"]',
            '[style*="clip: rect(0"]',
            '[style*="clip-path:inset(100%)"]',
            '[style*="clip-path: inset(100%)"]',
            '[style*="Clip-Path:inset(100%)"]',
            # Transform-based hiding
            '[style*="transform:scale(0)"]',
            '[style*="transform: scale(0)"]',
            '[style*="transform:translate(-999"]',
            '[style*="transform: translate(-999"]',
            '[style*="Transform:Scale(0)"]',
            # Content-visibility hiding
            '[style*="content-visibility:hidden"]',
            '[style*="content-visibility: hidden"]',
            '[style*="Content-Visibility:hidden"]',
            # Color-based hiding (transparent or same as background)
            '[style*="color:transparent"]',
            '[style*="color: transparent"]',
            '[style*="Color:Transparent"]',
            # Common CSS class-based hiding (selectolax supports :has, :not)
            ".hidden",
            ".invisible",
            ".hide",
            ".sr-only",  # Screen reader only - visually hidden
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
                match = re.match(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))(?:[a-z%]*)$", normalized_value)
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
                match = re.match(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))(?:[a-z%]*)$", normalized_value)
                if match is None:
                    continue
                try:
                    if float(match.group(1)) <= threshold:
                        return True
                except ValueError:
                    continue
            return False

        # Snapshot nodes before iterating: decompose() mutates the tree and
        # invalidates a live css("*") iterator, potentially skipping descendants.
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
        """
        Remove dangerous content from HTML elements.

        Security: This prevents XSS via:
        - Event handler attributes (onclick, onerror, onload, etc.)
        - javascript: URLs in href/src/action attributes
        - data: URLs with embedded scripts
        - vbscript: URLs (IE-specific but still valid)

        Returns:
            Dict with counts of removed items by category.
        """
        result = {
            "event_handlers": 0,
            "style_attributes": 0,
            "javascript_urls": 0,
            "data_urls": 0,
            "vbscript_urls": 0,
        }

        # Attributes that can contain URLs
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

        # Iterate through all elements with attributes
        for node in tree.css("*"):
            if not hasattr(node, "attributes") or not node.attributes:
                continue

            attrs_to_remove = []

            for attr_name in list(node.attributes.keys()):
                attr_value = node.attributes.get(attr_name)
                if not attr_value:
                    continue

                attr_name_lower = attr_name.lower()

                # srcset-style attributes can embed multiple candidate URLs.
                # Parsing every descriptor safely is error-prone, so drop them
                # entirely rather than trying to sanitize individual entries.
                if attr_name_lower in {"srcset", "imagesrcset"}:
                    attrs_to_remove.append(attr_name)
                    result["data_urls"] += 1
                    continue

                # Remove event handlers (on* attributes)
                if attr_name_lower.startswith("on"):
                    attrs_to_remove.append(attr_name)
                    result["event_handlers"] += 1
                    continue

                # Inline CSS is not needed for markdown extraction and can hide
                # active content via url(), expression(), or javascript: payloads.
                if attr_name_lower == "style":
                    attrs_to_remove.append(attr_name)
                    result["style_attributes"] += 1
                    continue

                # Check URL attributes for dangerous schemes
                if attr_name_lower in url_attributes:
                    # Normalize and strip whitespace for scheme check
                    # Handle obfuscation like "  javascript:" or "\tjavascript:"
                    clean_value = str(attr_value).strip().lower()

                    # Strip leading whitespace and control characters
                    clean_value = "".join(c for c in clean_value if c not in "\t\n\r\x0b\x0c")

                    if clean_value.startswith("javascript:"):
                        attrs_to_remove.append(attr_name)
                        result["javascript_urls"] += 1
                    elif clean_value.startswith("vbscript:"):
                        attrs_to_remove.append(attr_name)
                        result["vbscript_urls"] += 1
                    elif clean_value.startswith("data:"):
                        # Block all data: URLs except an explicit safe-image allowlist.
                        # data:,... (no media type) defaults to application/octet-stream
                        # in browsers and can carry executable content.
                        data_content = clean_value[5:]  # Remove 'data:'
                        comma_pos = data_content.find(",")
                        media_type_part = (
                            data_content[:comma_pos].split(";")[0].strip()
                            if comma_pos != -1
                            else ""
                        )
                        if not media_type_part:
                            media_type_part = "application/octet-stream"
                        if media_type_part not in _SAFE_DATA_URL_MEDIA_TYPES:
                            attrs_to_remove.append(attr_name)
                            result["data_urls"] += 1

            for attr_name in attrs_to_remove:
                try:
                    del node.attrs[attr_name]
                except Exception:
                    # selectolax may not support del on all node types
                    try:
                        node.attrs[attr_name] = ""  # type: ignore[assignment]
                    except Exception:
                        logger.debug(
                            "Unable to remove attribute %s on <%s>", attr_name, node.tag
                        )

        return result
