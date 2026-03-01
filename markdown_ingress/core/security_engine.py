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


class SecurityEngine:
    """
    Advanced security engine combining basic heuristics with Nova Framework.

    Modes:
    - basic: Only pattern-based detection (~5ms)
    - advanced: Patterns + Nova semantics (~50ms)
    - llm: Patterns + Nova semantics + LLM (~2s)
    """

    def __init__(
        self, strict: bool = False, advanced_security: bool = False, use_llm: bool = False
    ):
        self.strict = strict
        self.advanced_security = advanced_security
        self.use_llm = use_llm

        # Initialize basic security analyzer
        self.basic_analyzer = SecurityAnalyzer(strict=strict)

        # Initialize Nova if available and requested
        self.nova = None
        if self.advanced_security and NOVA_AVAILABLE:
            try:
                self.nova = NovaGuard(
                    enable_keywords=True, enable_semantics=True, enable_llm=self.use_llm
                )
                logger.info("Nova-tracer initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize Nova-tracer: {e}")
                self.nova = None

    def analyze(self, markdown: str, metadata: dict) -> dict:
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
        hidden_detected = metadata.get("hidden_elements_count", 0) > 0
        basic_analysis = self.basic_analyzer.analyze(markdown, hidden_content_detected=hidden_detected)
        basic_score = basic_analysis.score

        # Tier 2+3: Nova Framework (CONDITIONAL)
        nova_score = 0.0
        nova_details = {}
        scan_method = "basic"

        if self.nova and (basic_score > 0.3 or self.advanced_security or self.strict):
            try:
                nova_result = self.nova.scan(markdown)
                nova_score = nova_result["score"]
                nova_details = nova_result
                scan_method = "nova_llm" if self.use_llm else "nova_semantic"
                logger.info(
                    f"Nova scan: score={nova_score:.3f}, time={nova_details['scan_time_ms']:.0f}ms"
                )
            except Exception as e:
                logger.error(f"Nova scan failed: {e}")
                nova_score = 0.0
                nova_details = {"error": str(e)}

        # Combine scores (max of basic and weighted nova)
        # Nova is generally more accurate, so weight it higher
        final_score = max(basic_score, nova_score * 1.2)
        final_score = min(final_score, 1.0)  # Cap at 1.0

        # Generate flags
        flags = self._generate_flags(basic_analysis, nova_details)

        return {
            "injection_score": final_score,
            "basic_score": basic_score,
            "nova_score": nova_score,
            "nova_details": nova_details,
            "flags": flags,
            "scan_method": scan_method,
            "nova_available": NOVA_AVAILABLE,
            "nova_used": self.nova is not None,
            "pattern_matches": basic_analysis.pattern_matches,
            "imperative_density": basic_analysis.imperative_density,
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

        return list(set(flags))  # Unique flags
