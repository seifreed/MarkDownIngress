"""
Security Engine - Orchestrates basic and advanced injection detection.

Implements progressive security scanning:
1. Basic pattern detection (ALWAYS, ~5ms)
2. Nova semantic detection (if basic_score > 0.3, ~50ms)
3. Nova LLM detection (if use_llm=True, ~2s)
"""

import logging
import math
from dataclasses import dataclass
from typing import Any

from markdown_ingress.core import security as security_module
from markdown_ingress.core.nova_guard import NOVA_AVAILABLE, NovaGuard
from markdown_ingress.core.security_validation import (
    effective_security_thresholds,
    resolve_exception_fallback_score,
)
from markdown_ingress.core.security_validation import (
    ensure_bool as _ensure_bool,
)
from markdown_ingress.core.security_validation import (
    ensure_numeric_score_input as _ensure_numeric_score_input,
)

logger = logging.getLogger(__name__)

# Score assigned when Nova is disabled (no rules loaded) — basic analysis dominates.
_NOVA_DISABLED_SCORE: float = 0.0


@dataclass(frozen=True)
class SecurityExplanationContext:
    final_score: float
    basic_analysis: Any
    nova_details: dict
    scan_method: str
    block_threshold: float = 0.7
    warn_threshold: float = 0.4


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    """Return unique values while preserving first-seen order."""
    return list(dict.fromkeys(values))


class SecurityEngine:
    """
    Advanced security engine combining basic heuristics with Nova Framework.

    Modes:
    - basic: Only pattern-based detection (~5ms)
    - advanced: Patterns + Nova semantics (~50ms)
    - llm: Patterns + Nova semantics + LLM (~2s)
    """

    # Default fallback score when Nova scan fails or returns None.
    # Conservative value: higher score = more suspicious.
    DEFAULT_EXCEPTION_FALLBACK_SCORE = 0.75
    DEFAULT_NONE_SCORE = DEFAULT_EXCEPTION_FALLBACK_SCORE

    def __init__(
        self,
        strict: bool = False,
        advanced_security: bool = False,
        use_llm: bool = False,
        exception_fallback_score: float | None = None,
    ):
        self.strict = _ensure_bool("strict", strict)
        self.advanced_security = _ensure_bool("advanced_security", advanced_security)
        self.use_llm = _ensure_bool("use_llm", use_llm)
        self.exception_fallback_score = resolve_exception_fallback_score(
            exception_fallback_score,
            default=self.DEFAULT_EXCEPTION_FALLBACK_SCORE,
            minimum=0.5,
            logger=logger,
        )

        # Initialize basic security analyzer
        self.basic_analyzer = security_module.SecurityAnalyzer(strict=self.strict)

        # Initialize Nova if available and requested
        self.nova = None
        self._nova_init_failed = False
        if self.advanced_security:
            if not NOVA_AVAILABLE:
                self._nova_init_failed = True
                logger.warning(
                    "advanced_security=True but nova-hunting is not installed. "
                    "Falling back to basic pattern detection only."
                )
            else:
                try:
                    self.nova = NovaGuard(
                        enable_keywords=True, enable_semantics=True, enable_llm=self.use_llm
                    )
                    logger.info("Nova-tracer initialized successfully")
                except Exception as e:
                    self._nova_init_failed = True
                    logger.warning(
                        f"advanced_security=True but Nova-tracer failed to initialize: {e}. "
                        "Falling back to basic pattern detection only."
                    )

    def _parse_nova_result(self, markdown: str) -> tuple[float, dict, str]:
        """Run Nova scan and parse the result into (nova_score, nova_details, scan_method)."""
        if self.nova is None:
            return _NOVA_DISABLED_SCORE, {}, "basic"
        try:
            nova_result = self.nova.scan(markdown)
            return self._parse_scanned_nova_result(nova_result)
        except Exception as e:
            logger.error(f"Nova scan failed: {e}")
            return (
                self.exception_fallback_score,
                {"error": str(e), "scan_incomplete": True},
                "nova_error",
            )

    def _nova_scan_method(self) -> str:
        return "nova_llm" if self.use_llm else "nova_semantic"

    def _nova_fallback_result(self, nova_result: dict) -> tuple[float, dict, str]:
        return self.exception_fallback_score, nova_result, self._nova_scan_method()

    def _parse_scanned_nova_result(self, nova_result: dict | None) -> tuple[float, dict, str]:
        if nova_result is None:
            logger.warning(
                "Nova returned None, using fallback score: %s", self.exception_fallback_score
            )
            return (
                self.exception_fallback_score,
                {"error": "Nova returned None", "scan_incomplete": True},
                "nova_failed",
            )

        nova_score_raw = nova_result.get("score")
        if nova_score_raw is None:
            return self._parse_nova_missing_score(nova_result)

        nova_score_raw = self._coerce_nova_score(nova_score_raw)
        if nova_score_raw is None:
            return self._nova_fallback_result(nova_result)
        return self._successful_nova_result(nova_result, nova_score_raw)

    def _parse_nova_missing_score(self, nova_result: dict) -> tuple[float, dict, str]:
        severity = nova_result.get("severity")
        if severity == "disabled":
            logger.debug("Nova scanner disabled (no rules loaded), using basic analysis only")
            return _NOVA_DISABLED_SCORE, {}, "basic"
        if severity is not None:
            logger.warning(
                "Nova returned severity '%s' without score, using fallback: %s",
                severity,
                self.exception_fallback_score,
            )
            return self._nova_fallback_result(nova_result)
        logger.debug("Nova returned None score, falling back to basic analysis")
        return _NOVA_DISABLED_SCORE, nova_result, "basic"

    def _coerce_nova_score(self, nova_score_raw: object) -> float | None:
        try:
            nova_score = _ensure_numeric_score_input("Nova score", nova_score_raw)
        except (TypeError, ValueError):
            logger.warning(
                "Nova returned non-numeric score %r, using fallback: %s",
                nova_score_raw,
                self.exception_fallback_score,
            )
            return None
        if math.isnan(nova_score) or math.isinf(nova_score):
            logger.warning(
                "Nova returned non-finite score, using fallback: %s",
                self.exception_fallback_score,
            )
            return None
        return nova_score

    def _successful_nova_result(
        self, nova_result: dict, nova_score_raw: float
    ) -> tuple[float, dict, str]:
        nova_score = max(0.0, min(1.0, nova_score_raw))
        scan_time = nova_result.get("scan_time_ms")
        if scan_time is not None:
            logger.info(f"Nova scan: score={nova_score:.3f}, time={scan_time:.0f}ms")
        else:
            logger.info(f"Nova scan: score={nova_score:.3f} (scan incomplete)")
        return nova_score, nova_result, self._nova_scan_method()

    @staticmethod
    def effective_thresholds(
        block_threshold: float = 0.7,
        warn_threshold: float = 0.4,
        *,
        strict: bool = False,
    ) -> tuple[float, float]:
        """Return policy thresholds after applying strict-mode tightening."""
        return effective_security_thresholds(
            block_threshold,
            warn_threshold,
            strict=strict,
            logger=logger,
        )

    def analyze(
        self,
        markdown: str,
        metadata: dict,
        *,
        block_threshold: float = 0.7,
        warn_threshold: float = 0.4,
    ) -> dict:
        """
        Analyze text for prompt injection.

        Returns dict with:
        - injection_score: Combined score (0.0-1.0)
        - basic_score: Pattern-based score
        - nova_score: Nova Framework score (if used)
        - nova_details: Detailed Nova results
        - flags: List of security flags
        - scan_method: Which methods were used
        """

        # Tier 1: Basic pattern detection (ALWAYS)
        # Validate metadata before trusting it
        hidden_detected = False
        if isinstance(metadata, dict):
            hidden_count = metadata.get("hidden_elements_count")
            if isinstance(hidden_count, (int, float)) and hidden_count > 0:
                hidden_detected = True
        basic_analysis = self.basic_analyzer.analyze(
            markdown, hidden_content_detected=hidden_detected
        )
        basic_score = basic_analysis.score

        # Tier 2+3: Nova Framework (CONDITIONAL)
        # Run Nova when available and basic score is non-zero (indicating potential risk).
        # Lowered threshold from 0.1 to 0.05 to prevent sophisticated attacks that
        # score just below the old threshold from bypassing semantic detection.
        # When advanced_security or strict is enabled, always run Nova.
        nova_score = _NOVA_DISABLED_SCORE
        nova_details: dict[str, Any] = {}
        scan_method = "basic"

        if self.nova and (
            math.isnan(basic_score) or basic_score >= 0.05 or self.advanced_security or self.strict
        ):
            nova_score, nova_details, scan_method = self._parse_nova_result(markdown)

        # Combine scores: when Nova was never invoked, use basic score directly.
        # When both signals exist, never allow combination to reduce the highest signal.
        if scan_method == "basic":
            final_score = basic_score
        else:
            final_score = max(basic_score, nova_score)

        block_threshold, warn_threshold = self.effective_thresholds(
            block_threshold,
            warn_threshold,
            strict=self.strict,
        )

        # Generate flags
        flags = self._generate_flags(basic_analysis, nova_details)

        if self._nova_init_failed:
            flags.append("warning:advanced_security_requested_but_unavailable")

        return {
            "injection_score": final_score,
            "basic_score": basic_score,
            "nova_score": nova_score,
            "nova_details": nova_details,
            "flags": flags,
            "scan_method": scan_method,
            "nova_available": NOVA_AVAILABLE,
            "nova_used": scan_method in ("nova_semantic", "nova_llm"),
            "advanced_security_available": not self._nova_init_failed,
            "pattern_matches": basic_analysis.pattern_matches,
            "imperative_density": basic_analysis.imperative_density,
            "explanation": self._build_explanation(
                SecurityExplanationContext(
                    final_score=final_score,
                    basic_analysis=basic_analysis,
                    nova_details=nova_details,
                    scan_method=scan_method,
                    block_threshold=block_threshold,
                    warn_threshold=warn_threshold,
                )
            ),
        }

    def _generate_flags(self, basic_analysis, nova_details: dict) -> list:
        """Generate list of security flags from all detection methods."""
        flags = []

        # Basic pattern flags
        flags.extend(basic_analysis.flags)

        # Nova flags
        if nova_details.get("matched_rules"):
            for rule in nova_details["matched_rules"]:
                flags.append(f"nova:{rule}")

        if nova_details.get("categories"):
            for category in nova_details["categories"]:
                flags.append(f"category:{category}")

        # Severity flag
        if nova_details.get("severity"):
            flags.append(f"severity:{nova_details['severity']}")

        return _dedupe_preserving_order(flags)

    def _build_explanation(
        self,
        context: SecurityExplanationContext,
    ) -> dict:
        """Produce actionable explainability data for downstream consumers."""
        triggers = []
        for match in context.basic_analysis.pattern_matches:
            samples = []
            for sample in match.get("samples", []):
                if isinstance(sample, tuple):
                    samples.append(" ".join(str(part) for part in sample if part))
                else:
                    samples.append(str(sample))
            triggers.append(
                {
                    "source": "pattern",
                    "name": match.get("pattern"),
                    "weight": match.get("weight"),
                    "occurrences": match.get("occurrences"),
                    "samples": samples,
                }
            )

        for rule in context.nova_details.get("matched_rules", []):
            triggers.append({"source": "nova", "name": rule})

        if math.isnan(context.final_score):
            recommendation = "block"
        else:
            recommendation = "allow"
            if context.final_score >= context.block_threshold:
                recommendation = "block"
            elif context.final_score >= context.warn_threshold:
                recommendation = "warn"

        return {
            "scan_method": context.scan_method,
            "recommendation": recommendation,
            "summary": (
                f"Detected {len(context.basic_analysis.pattern_matches)} heuristic pattern groups"
                f" with imperative density {context.basic_analysis.imperative_density:.3f}."
            ),
            "triggers": triggers,
            "hidden_content_detected": context.basic_analysis.hidden_content_detected,
        }
