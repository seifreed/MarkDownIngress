"""Sanitization helpers for readability extraction."""

from __future__ import annotations

import logging
import re
from typing import Any

from selectolax.parser import HTMLParser

from markdown_ingress.core.url_safety import (
    dangerous_url_scheme,
    normalize_url_value_for_scheme_detection,
)

logger = logging.getLogger(__name__)

HIDDEN_SELECTORS = (
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
)

_URL_ATTRIBUTES = frozenset(
    {
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
)
_SRCSET_ATTRIBUTES = frozenset({"srcset", "imagesrcset"})
_NUMERIC_STYLE_VALUE_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:[a-z%]*)$"
)


def remove_hidden_elements(tree: HTMLParser) -> int:
    """Remove hidden nodes and return how many were decomposed."""
    count = _remove_hidden_selector_matches(tree)
    return count + _remove_hidden_style_nodes(tree)


def sanitize_dangerous_content(tree: HTMLParser) -> dict[str, int]:
    """Remove dangerous HTML attributes from parsed content."""
    result = _new_sanitization_stats()
    for node in tree.css("*"):
        if not getattr(node, "attributes", None):
            continue
        attrs_to_remove = _dangerous_attributes_for_node(node, result)
        _remove_attributes(node, attrs_to_remove)
    return result


def _remove_hidden_selector_matches(tree: HTMLParser) -> int:
    count = 0
    decomposed_ids: set[int] = set()
    for selector in HIDDEN_SELECTORS:
        try:
            elements = tree.css(selector)
            for elem in elements:
                eid = id(elem)
                if eid in decomposed_ids:
                    continue
                decomposed_ids.add(eid)
                count += 1
                elem.decompose()
        except (AttributeError, ValueError, TypeError) as e:
            logger.debug("Selector '%s' not supported: %s", selector, e)
        except Exception as e:  # noqa: BLE001 - sanitizer skips unsupported DOM nodes
            logger.warning("Unexpected error processing selector '%s': %s", selector, e)
    return count


def _remove_hidden_style_nodes(tree: HTMLParser) -> int:
    count = 0
    for node in list(tree.css("*")):
        if not getattr(node, "attributes", None):
            continue
        if node.parent is None:
            continue
        style_value = node.attributes.get("style")
        if style_value and _style_hides_element(str(style_value).lower()):
            node.decompose()
            count += 1
    return count


def _style_hides_element(style_value: str) -> bool:
    return (
        _style_has_exact_zero(style_value, "opacity")
        or _style_has_exact_zero(style_value, "height")
        or _style_has_exact_zero(style_value, "width")
        or _style_has_exact_zero(style_value, "font-size")
        or _style_has_exact_keyword(style_value, "display", {"none"})
        or _style_has_exact_keyword(style_value, "visibility", {"hidden", "collapse"})
        or _style_has_exact_keyword(style_value, "content-visibility", {"hidden"})
        or _style_has_exact_keyword(style_value, "color", {"transparent"})
        or _style_has_exact_keyword(style_value, "clip-path", {"inset(100%)"})
        or _style_has_prefix_value(style_value, "clip", ("rect(0",))
        or _style_has_prefix_value(style_value, "transform", ("scale(0)", "translate(-999"))
        or _style_has_negative_offset(style_value, "left")
        or _style_has_negative_offset(style_value, "top")
    )


def _iter_style_property_values(style_value: str, property_name: str):
    for declaration in style_value.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        if name.strip().lower() == property_name:
            yield value


def _style_has_exact_zero(style_value: str, property_name: str) -> bool:
    for value in _iter_style_property_values(style_value, property_name):
        normalized_value = value.strip().lower()
        if normalized_value.endswith("!important"):
            normalized_value = normalized_value[: -len("!important")].strip()
        match = _NUMERIC_STYLE_VALUE_RE.match(normalized_value)
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
    for value in _iter_style_property_values(style_value, property_name):
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
    for value in _iter_style_property_values(style_value, property_name):
        normalized_value = re.sub(r"\s+", "", value.strip().lower())
        if normalized_value.endswith("!important"):
            normalized_value = normalized_value[: -len("!important")]
        if any(normalized_value.startswith(prefix) for prefix in prefixes):
            return True
    return False


def _style_has_negative_offset(
    style_value: str, property_name: str, threshold: float = -999.0
) -> bool:
    for value in _iter_style_property_values(style_value, property_name):
        normalized_value = re.sub(r"\s+", "", value.strip().lower())
        if normalized_value.endswith("!important"):
            normalized_value = normalized_value[: -len("!important")]
        match = _NUMERIC_STYLE_VALUE_RE.match(normalized_value)
        if match is None:
            continue
        try:
            if float(match.group(1)) <= threshold:
                return True
        except ValueError:
            continue
    return False


def _new_sanitization_stats() -> dict[str, int]:
    return {
        "event_handlers": 0,
        "style_attributes": 0,
        "javascript_urls": 0,
        "data_urls": 0,
        "vbscript_urls": 0,
    }


def _dangerous_attributes_for_node(node: Any, result: dict[str, int]) -> list[str]:
    attrs_to_remove = []
    for attr_name in list(node.attributes.keys()):
        attr_value = node.attributes.get(attr_name)
        if not attr_value:
            continue

        attr_name_lower = attr_name.lower()
        if attr_name_lower in _SRCSET_ATTRIBUTES:
            attrs_to_remove.append(attr_name)
            if _srcset_contains_data_url(attr_value):
                result["data_urls"] += 1
            continue

        if attr_name_lower.startswith("on"):
            attrs_to_remove.append(attr_name)
            result["event_handlers"] += 1
            continue

        if attr_name_lower == "style":
            attrs_to_remove.append(attr_name)
            result["style_attributes"] += 1
            continue

        if attr_name_lower in _URL_ATTRIBUTES:
            _mark_dangerous_url_attribute(attr_name, attr_value, attrs_to_remove, result)
    return attrs_to_remove


def _srcset_contains_data_url(attr_value: object) -> bool:
    return any(
        normalize_url_value_for_scheme_detection(part).startswith("data:")
        for part in str(attr_value).split(",")
    )


def _mark_dangerous_url_attribute(
    attr_name: str,
    attr_value: object,
    attrs_to_remove: list[str],
    result: dict[str, int],
) -> None:
    scheme = dangerous_url_scheme(attr_value)
    if scheme == "javascript":
        attrs_to_remove.append(attr_name)
        result["javascript_urls"] += 1
    elif scheme == "vbscript":
        attrs_to_remove.append(attr_name)
        result["vbscript_urls"] += 1
    elif scheme == "data":
        attrs_to_remove.append(attr_name)
        result["data_urls"] += 1


def _remove_attributes(node: Any, attrs_to_remove: list[str]) -> None:
    for attr_name in attrs_to_remove:
        try:
            del node.attrs[attr_name]
        except Exception as exc:  # noqa: BLE001 - sanitizer must neutralize best effort
            logger.warning(
                "Failed to delete dangerous attribute %s on <%s>: %s",
                attr_name,
                node.tag,
                exc,
            )
            try:
                node.attrs[attr_name] = ""
            except Exception:  # noqa: BLE001 - failed neutralization is logged below
                logger.warning(
                    "Unable to neutralize dangerous attribute %s on <%s>",
                    attr_name,
                    node.tag,
                )
