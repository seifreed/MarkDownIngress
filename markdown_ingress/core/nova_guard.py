"""
Nova-tracer integration for advanced prompt injection detection.

This integration is optional and degrades safely when NOVA rules are not configured.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from nova import NovaMatcher, NovaParser, NovaRule  # noqa: F401

    NOVA_AVAILABLE = True
except ImportError:  # pragma: no cover
    NOVA_AVAILABLE = False  # pragma: no cover
    logger.warning(  # pragma: no cover
        "nova-hunting not installed. Install with: pip install nova-hunting"
    )  # pragma: no cover

_BUNDLED_RULES_PATH = Path(__file__).parent.parent / "rules" / "prompt_injection.nova"


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
            raise ImportError("nova-hunting not installed")  # pragma: no cover

        self.enable_keywords = enable_keywords
        self.enable_semantics = enable_semantics
        self.enable_llm = enable_llm

        # Load NOVA rules
        if rules_path:
            parser = NovaParser()
            with open(rules_path) as f:
                self.rules = [parser.parse(f.read())]
        else:
            self.rules = self._load_bundled_rules()
        self.matchers: list = []
        if self.rules:
            for rule in self.rules:
                self.matchers.append(
                    NovaMatcher(rule=rule, create_llm_evaluator=enable_llm)
                )
        else:
            logger.warning(
                "Nova-tracer enabled but no rules were loaded. "
                "Provide rules_path to activate semantic/LLM scanning."
            )

    def _load_bundled_rules(self):
        """Load bundled NOVA rules for prompt injection detection."""
        if not _BUNDLED_RULES_PATH.exists():
            return []
        parser = NovaParser()
        rules = []
        with open(_BUNDLED_RULES_PATH) as f:
            content = f.read()
        # Parse each rule block individually
        import re
        for block in re.findall(r"rule\s+\w+\s*\{[^}]+(?:\{[^}]*\}[^}]*)?\}", content, re.DOTALL):
            try:
                rules.append(parser.parse(block.strip()))
            except Exception as e:
                logger.debug("Failed to parse bundled rule: %s", e)
        return rules

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

        if not self.matchers:
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

        scores = []
        matched_rules = []
        categories = []
        for matcher in self.matchers:
            result = matcher.check_prompt(text)
            if result.get("matched"):
                scores.append(1.0)
                matched_rules.append(result.get("rule_name", "unknown"))
                meta = result.get("meta", {})
                if "category" in meta:
                    categories.append(meta["category"])
            else:
                scores.append(0.0)

        score = max(scores) if scores else 0.0
        scan_time_ms = (time.time() - start) * 1000

        return {
            "score": score,
            "severity": "high" if score >= 0.7 else "medium" if score >= 0.3 else "low",
            "matched_rules": matched_rules,
            "categories": categories,
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
