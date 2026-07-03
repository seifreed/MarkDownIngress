"""
Security Engine - Orchestrates basic and advanced injection detection.

Implements progressive security scanning:
1. Basic pattern detection (ALWAYS, ~5ms)
2. Nova semantic detection (if basic_score > 0.3, ~50ms)
3. Nova LLM detection (if use_llm=True, ~2s)
"""

import logging
import math

from markdown_ingress.core import security as security_module
from markdown_ingress.core.nova_guard import NOVA_AVAILABLE, NovaGuard
from markdown_ingress.core.security_explanation import (
    SecurityExplanationContext,
    build_security_explanation,
)
from markdown_ingress.core.security_nova_result import NOVA_DISABLED_SCORE, parse_nova_result
from markdown_ingress.core.security_validation import (
    effective_security_thresholds,
    resolve_exception_fallback_score,
)
from markdown_ingress.core.security_validation import (
    ensure_bool as _ensure_bool,
)

logger = logging.getLogger(__name__)
NOVA_SCAN_FAILURE_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)


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
                except (ImportError, OSError, RuntimeError, TypeError, ValueError) as e:
                    self._nova_init_failed = True
                    logger.warning(
                        f"advanced_security=True but Nova-tracer failed to initialize: {e}. "
                        "Falling back to basic pattern detection only."
                    )

    @staticmethod
    def _hidden_content_detected(metadata: dict) -> bool:
        hidden_count = metadata.get("hidden_elements_count")
        return isinstance(hidden_count, (int, float)) and hidden_count > 0

    @staticmethod
    def _nova_required(advanced_security: bool, strict: bool, basic_score: float) -> bool:
        return advanced_security or strict or math.isnan(basic_score) or basic_score >= 0.05

    @staticmethod
    def _compute_final_score(scan_method: str, basic_score: float, nova_score: float) -> float:
        return basic_score if scan_method == "basic" else max(basic_score, nova_score)

    def _scan_with_nova_if_applicable(
        self, markdown: str, basic_score: float
    ) -> tuple[float, dict, str]:
        if not self.nova:
            return NOVA_DISABLED_SCORE, {}, "basic"

        if not self._nova_required(self.advanced_security, self.strict, basic_score):
            return NOVA_DISABLED_SCORE, {}, "basic"

        return self._parse_nova_result(markdown)

    def _parse_nova_result(self, markdown: str) -> tuple[float, dict, str]:
        """Run Nova scan and parse the result into (nova_score, nova_details, scan_method)."""
        if self.nova is None:
            return NOVA_DISABLED_SCORE, {}, "basic"
        try:
            nova_result = self.nova.scan(markdown)
            return parse_nova_result(
                nova_result,
                exception_fallback_score=self.exception_fallback_score,
                use_llm=self.use_llm,
                logger=logger,
            )
        except NOVA_SCAN_FAILURE_ERRORS as e:
            logger.exception("Nova scan failed")
            return (
                self.exception_fallback_score,
                {"error": str(e), "scan_incomplete": True},
                "nova_error",
            )

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

        hidden_detected = self._hidden_content_detected(
            metadata if isinstance(metadata, dict) else {}
        )
        basic_analysis = self.basic_analyzer.analyze(
            markdown, hidden_content_detected=hidden_detected
        )
        basic_score = basic_analysis.score
        nova_score, nova_details, scan_method = self._scan_with_nova_if_applicable(
            markdown, basic_score
        )
        final_score = self._compute_final_score(scan_method, basic_score, nova_score)

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
            "explanation": build_security_explanation(
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
            flags.extend(f"nova:{rule}" for rule in nova_details["matched_rules"])

        if nova_details.get("categories"):
            flags.extend(f"category:{category}" for category in nova_details["categories"])

        # Severity flag
        if nova_details.get("severity"):
            flags.append(f"severity:{nova_details['severity']}")

        return _dedupe_preserving_order(flags)
