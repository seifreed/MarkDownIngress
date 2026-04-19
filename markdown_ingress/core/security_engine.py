"""
Security Engine - Orchestrates basic and advanced injection detection.

Implements progressive security scanning:
1. Basic pattern detection (ALWAYS, ~5ms)
2. Nova semantic detection (if basic_score > 0.3, ~50ms)
3. Nova LLM detection (if use_llm=True, ~2s)
"""

import logging

from markdown_ingress.core.nova_guard import NOVA_AVAILABLE, NovaGuard
from markdown_ingress.core.security import SecurityAnalyzer

logger = logging.getLogger(__name__)

# Score assigned when Nova is disabled (no rules loaded) — basic analysis dominates.
_NOVA_DISABLED_SCORE: float = 0.0


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
        self.strict = strict
        self.advanced_security = advanced_security
        self.use_llm = use_llm
        # Score to use when Nova scan throws an exception (malicious payloads shouldn't bypass)
        # Validate score is within valid range [0.0, 1.0]
        # BUG FIX: Enforce minimum of 0.5 to prevent complete security bypass
        min_fallback_score = 0.5
        if exception_fallback_score is not None:
            if not (0.0 <= exception_fallback_score <= 1.0):
                logger.warning(
                    "Invalid exception_fallback_score '%s', must be 0.0-1.0. Using default 0.75.",
                    exception_fallback_score,
                )
                self.exception_fallback_score = self.DEFAULT_EXCEPTION_FALLBACK_SCORE
            elif exception_fallback_score < min_fallback_score:
                logger.warning(
                    "exception_fallback_score '%s' is below safe minimum %.1f. "
                    "Setting to %.1f to prevent security bypass.",
                    exception_fallback_score,
                    min_fallback_score,
                    min_fallback_score,
                )
                self.exception_fallback_score = min_fallback_score
            else:
                self.exception_fallback_score = exception_fallback_score
        else:
            self.exception_fallback_score = self.DEFAULT_EXCEPTION_FALLBACK_SCORE

        # Initialize basic security analyzer
        self.basic_analyzer = SecurityAnalyzer(strict=strict)

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
        try:
            nova_result = self.nova.scan(markdown)
            if nova_result is None:
                logger.warning("Nova returned None, using fallback score: %s", self.exception_fallback_score)
                return self.exception_fallback_score, {"error": "Nova returned None", "scan_incomplete": True}, "nova_failed"

            nova_score_raw = nova_result.get("score")
            if nova_score_raw is None:
                if nova_result.get("severity") == "disabled":
                    logger.debug("Nova scanner disabled (no rules loaded), using basic analysis only")
                    return _NOVA_DISABLED_SCORE, {}, "basic"
                logger.debug("Nova returned None score, using default: %s", self.exception_fallback_score)
                return self.exception_fallback_score, nova_result, "nova_llm" if self.use_llm else "nova_semantic"

            nova_score = max(0.0, min(1.0, nova_score_raw))
            scan_method = "nova_llm" if self.use_llm else "nova_semantic"
            scan_time = nova_result.get("scan_time_ms")
            if scan_time is not None:
                logger.info(f"Nova scan: score={nova_score:.3f}, time={scan_time:.0f}ms")
            else:
                logger.info(f"Nova scan: score={nova_score:.3f} (scan incomplete)")
            return nova_score, nova_result, scan_method
        except Exception as e:
            logger.error(f"Nova scan failed: {e}")
            return self.exception_fallback_score, {"error": str(e), "scan_incomplete": True}, "nova_error"

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
        nova_details = {}
        scan_method = "basic"

        if self.nova and (basic_score > 0.05 or self.advanced_security or self.strict):
            nova_score, nova_details, scan_method = self._parse_nova_result(markdown)

        # Combine scores: when Nova was never invoked, use basic score directly.
        # When both signals exist, never allow combination to reduce the highest signal.
        if scan_method == "basic":
            final_score = basic_score
        else:
            final_score = max(basic_score, nova_score)

        # Adjust thresholds in strict mode for more aggressive blocking
        if self.strict:
            block_threshold = min(block_threshold, 0.5)
            warn_threshold = min(warn_threshold, 0.3)

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
                final_score=final_score,
                basic_analysis=basic_analysis,
                nova_details=nova_details,
                scan_method=scan_method,
                block_threshold=block_threshold,
                warn_threshold=warn_threshold,
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
        *,
        final_score: float,
        basic_analysis,
        nova_details: dict,
        scan_method: str,
        block_threshold: float = 0.7,
        warn_threshold: float = 0.4,
    ) -> dict:
        """Produce actionable explainability data for downstream consumers."""
        triggers = []
        for match in basic_analysis.pattern_matches:
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

        for rule in nova_details.get("matched_rules", []):
            triggers.append({"source": "nova", "name": rule})

        recommendation = "allow"
        if final_score >= block_threshold:
            recommendation = "block"
        elif final_score >= warn_threshold:
            recommendation = "warn"

        return {
            "scan_method": scan_method,
            "recommendation": recommendation,
            "summary": (
                f"Detected {len(basic_analysis.pattern_matches)} heuristic pattern groups"
                f" with imperative density {basic_analysis.imperative_density:.3f}."
            ),
            "triggers": triggers,
            "hidden_content_detected": basic_analysis.hidden_content_detected,
        }
