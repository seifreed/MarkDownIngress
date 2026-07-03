"""Language metadata extraction helpers."""

from __future__ import annotations

import logging
from typing import Any, cast

from selectolax.parser import HTMLParser

from markdown_ingress.runtime_helpers import load_optional_module, load_optional_object

logger = logging.getLogger(__name__)
LANGDETECT_SAMPLE_CHARS = 5000


def empty_language_info() -> dict[str, Any]:
    return {"language": None, "source": None, "confidence": None}


def language_info(language: str, source: str, confidence: float) -> dict[str, Any]:
    return {"language": language, "source": source, "confidence": confidence}


def normalize_language_code(value: str, *, normalize_multilingual: bool) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    # Content-language and lang attributes can contain comma-separated
    # language lists. Use the primary declared language instead of returning
    # the whole list as if it were a single code.
    raw = raw.split(",", 1)[0].strip()
    if not raw:
        return ""
    return raw.split("-")[0] if normalize_multilingual else raw


def language_info_from_value(
    value: str,
    *,
    source: str,
    confidence: float,
    normalize_multilingual: bool,
) -> dict[str, Any] | None:
    lang_code = normalize_language_code(value, normalize_multilingual=normalize_multilingual)
    if lang_code:
        return language_info(lang_code, source, confidence)
    return None


def extract_declared_language_info(
    parser: HTMLParser,
    *,
    normalize_multilingual: bool,
) -> dict[str, Any] | None:
    html_tag = parser.css_first("html")
    if html_tag:
        lang = (html_tag.attributes.get("lang") or "").strip()
        if lang:
            return language_info_from_value(
                lang,
                source="html_lang",
                confidence=1.0,
                normalize_multilingual=normalize_multilingual,
            )

    meta_lang = parser.css_first('meta[http-equiv="content-language"]')
    if meta_lang:
        content = (meta_lang.attributes.get("content") or "").strip()
        if content:
            return language_info_from_value(
                content,
                source="meta_content_language",
                confidence=0.95,
                normalize_multilingual=normalize_multilingual,
            )
    return None


def detect_content_language_info(
    parser: HTMLParser,
    *,
    normalize_multilingual: bool,
) -> dict[str, Any] | None:
    langdetect_exception_type: type[BaseException] = Exception
    try:
        langdetect = cast(Any, load_optional_module("langdetect", purpose="language detection"))
        langdetect_exceptions = cast(
            Any,
            load_optional_object(
                "langdetect",
                "LangDetectException",
                purpose="language detection",
            ),
        )
        langdetect_exception_type = cast(type[BaseException], langdetect_exceptions)

        # langdetect seeds its detector randomly per process, so the same text
        # can resolve to different languages across runs. Pin the seed for
        # deterministic output.
        langdetect.DetectorFactory.seed = 0

        body = parser.css_first("body")
        if body:
            text = body.text(strip=True)
            if text and len(text) > 50:
                detected_lang = normalize_language_code(
                    cast(str, langdetect.detect(text[:LANGDETECT_SAMPLE_CHARS])),
                    normalize_multilingual=normalize_multilingual,
                )
                if detected_lang:
                    return language_info(detected_lang, "langdetect", 0.6)
    except ImportError:
        return None
    except (
        AttributeError,
        langdetect_exception_type,
        TypeError,
        ValueError,
    ) as exc:
        logger.debug("langdetect failed: %s", exc)
    return None
