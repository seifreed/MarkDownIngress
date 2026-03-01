"""
Nova-tracer integration for advanced prompt injection detection.

This integration is optional and degrades safely when NOVA rules are not configured.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from nova_hunting import NovaScanner, load_rules

    NOVA_AVAILABLE = True
except ImportError:
    NOVA_AVAILABLE = False
    logger.warning(
        "nova-hunting not installed. Install with: pip install markdown-ingress[security]"
    )


class NovaGuard:
    """Advanced prompt injection detection using Nova Framework."""

    def __init__(
        self,
        enable_keywords: bool = True,
        enable_semantics: bool = True,
        enable_llm: bool = False,
        rules_path: str | None = None,
    ):
        if not NOVA_AVAILABLE:
            raise ImportError("nova-hunting not installed")

        self.enable_keywords = enable_keywords
        self.enable_semantics = enable_semantics
        self.enable_llm = enable_llm

        # Load NOVA rules
        if rules_path:
            self.rules = load_rules(rules_path)
        else:
            self.rules = self._load_bundled_rules()

        # Initialize scanner only if we have actual rules.
        self.scanner = None
        if self.rules:
            self.scanner = NovaScanner(
                rules=self.rules,
                enable_keywords=enable_keywords,
                enable_semantics=enable_semantics,
                enable_llm=enable_llm,
            )
        else:
            logger.warning(
                "Nova-tracer enabled but no rules were loaded. "
                "Provide rules_path to activate semantic/LLM scanning."
            )

    def _load_bundled_rules(self):
        """Load bundled NOVA rules for prompt injection detection."""
        # No bundled rules yet. Caller must provide rules_path.
        return []

    def scan(self, text: str) -> dict:
        """
        Scan text for prompt injection attempts.

        Args:
            text: Text to scan

        Returns:
            dict with score, severity, matched_rules, categories, scan_time_ms
        """
        import time

        start = time.time()

        if self.scanner is None:
            return {
                "score": 0.0,
                "severity": "unknown",
                "matched_rules": [],
                "categories": [],
                "scan_time_ms": 0.0,
                "rules_loaded": 0,
                "disabled_reason": "no_rules_configured",
                "tiers_used": {
                    "keywords": False,
                    "semantics": False,
                    "llm": False,
                },
            }

        result = self.scanner.scan(text)
        scan_time_ms = (time.time() - start) * 1000

        return {
            "score": result.score if hasattr(result, "score") else 0.0,
            "severity": result.severity if hasattr(result, "severity") else "low",
            "matched_rules": result.rules if hasattr(result, "rules") else [],
            "categories": result.categories if hasattr(result, "categories") else [],
            "scan_time_ms": scan_time_ms,
            "rules_loaded": len(self.rules),
            "tiers_used": {
                "keywords": self.enable_keywords,
                "semantics": self.enable_semantics,
                "llm": self.enable_llm,
            },
        }

    @staticmethod
    def is_available() -> bool:
        """Check if nova-hunting is installed."""
        return NOVA_AVAILABLE
