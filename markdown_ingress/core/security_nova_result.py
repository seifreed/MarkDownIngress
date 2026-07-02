"""Result parsing for optional Nova security scans."""

from __future__ import annotations

import logging
import math

from markdown_ingress.core.security_validation import (
    ensure_numeric_score_input as _ensure_numeric_score_input,
)

NOVA_DISABLED_SCORE: float = 0.0


def parse_nova_result(
    nova_result: dict | None,
    *,
    exception_fallback_score: float,
    use_llm: bool,
    logger: logging.Logger,
) -> tuple[float, dict, str]:
    if nova_result is None:
        logger.warning("Nova returned None, using fallback score: %s", exception_fallback_score)
        return (
            exception_fallback_score,
            {"error": "Nova returned None", "scan_incomplete": True},
            "nova_failed",
        )

    nova_score_raw = nova_result.get("score")
    if nova_score_raw is None:
        return _parse_missing_score(
            nova_result,
            exception_fallback_score=exception_fallback_score,
            use_llm=use_llm,
            logger=logger,
        )

    nova_score = _coerce_nova_score(
        nova_score_raw,
        exception_fallback_score=exception_fallback_score,
        logger=logger,
    )
    if nova_score is None:
        return exception_fallback_score, nova_result, _scan_method(use_llm)
    return _successful_nova_result(nova_result, nova_score, use_llm=use_llm, logger=logger)


def _parse_missing_score(
    nova_result: dict,
    *,
    exception_fallback_score: float,
    use_llm: bool,
    logger: logging.Logger,
) -> tuple[float, dict, str]:
    severity = nova_result.get("severity")
    if severity == "disabled":
        logger.debug("Nova scanner disabled (no rules loaded), using basic analysis only")
        return NOVA_DISABLED_SCORE, {}, "basic"
    if severity is not None:
        logger.warning(
            "Nova returned severity '%s' without score, using fallback: %s",
            severity,
            exception_fallback_score,
        )
        return exception_fallback_score, nova_result, _scan_method(use_llm)
    logger.debug("Nova returned None score, falling back to basic analysis")
    return NOVA_DISABLED_SCORE, nova_result, "basic"


def _coerce_nova_score(
    nova_score_raw: object,
    *,
    exception_fallback_score: float,
    logger: logging.Logger,
) -> float | None:
    try:
        nova_score = _ensure_numeric_score_input("Nova score", nova_score_raw)
    except (TypeError, ValueError):
        logger.warning(
            "Nova returned non-numeric score %r, using fallback: %s",
            nova_score_raw,
            exception_fallback_score,
        )
        return None
    if math.isnan(nova_score) or math.isinf(nova_score):
        logger.warning(
            "Nova returned non-finite score, using fallback: %s",
            exception_fallback_score,
        )
        return None
    return nova_score


def _successful_nova_result(
    nova_result: dict,
    nova_score_raw: float,
    *,
    use_llm: bool,
    logger: logging.Logger,
) -> tuple[float, dict, str]:
    nova_score = max(0.0, min(1.0, nova_score_raw))
    scan_time = nova_result.get("scan_time_ms")
    if scan_time is not None:
        logger.info("Nova scan: score=%.3f, time=%.0fms", nova_score, scan_time)
    else:
        logger.info("Nova scan: score=%.3f (scan incomplete)", nova_score)
    return nova_score, nova_result, _scan_method(use_llm)


def _scan_method(use_llm: bool) -> str:
    return "nova_llm" if use_llm else "nova_semantic"
