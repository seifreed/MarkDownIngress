"""
Nova-tracer integration for advanced prompt injection detection.

This integration is optional and degrades safely when NOVA rules are not configured.
"""

import contextlib
import io
import logging
from importlib import import_module
from pathlib import Path
from typing import Any

from markdown_ingress.core.nova_rules import (
    parse_bundled_rule_content,
    parse_rule_content,
    read_rules_file_atomically,
    reject_unsafe_rules_path,
)
from markdown_ingress.core.security_validation import (
    ensure_bool as _ensure_bool,
)
from markdown_ingress.core.security_validation import (
    ensure_severity_threshold as _ensure_threshold,
)

logger = logging.getLogger(__name__)

NovaMatcher: Any
NovaParser: Any

try:
    _nova_module = import_module("nova")
    NovaMatcher = getattr(_nova_module, "NovaMatcher")
    NovaParser = getattr(_nova_module, "NovaParser")

    NOVA_AVAILABLE = True
except ImportError:  # pragma: no cover
    NOVA_AVAILABLE = False  # pragma: no cover

_BUNDLED_RULES_PATH = Path(__file__).parent.parent / "rules" / "prompt_injection.nova"

# Default severity thresholds (can be overridden via constructor)
DEFAULT_HIGH_THRESHOLD = 0.7
DEFAULT_MEDIUM_THRESHOLD = 0.3


def _coerce_rule_score(raw_score: object, rule_name: str) -> float:
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
        logger.warning(
            "Rule '%s' returned invalid score %r, defaulting to 0.5",
            rule_name,
            raw_score,
        )
        return 0.5
    score = float(raw_score)
    if score != score or score in (float("inf"), float("-inf")):
        logger.warning(
            "Rule '%s' returned non-finite score %r, defaulting to 0.5",
            rule_name,
            raw_score,
        )
        return 0.5
    if not 0.0 <= score <= 1.0:
        logger.warning("Rule '%s' returned out-of-range score %r, clamping", rule_name, raw_score)
        return max(0.0, min(1.0, score))
    return score


class NovaGuard:
    """Advanced prompt injection detection using Nova Framework."""

    # Detection profile constructor keeps optional engines and thresholds explicit.
    def __init__(  # noqa: PLR0913
        self,
        enable_keywords: bool = True,
        enable_semantics: bool = True,
        enable_llm: bool = False,
        rules_path: str | None = None,
        *,
        severity_high_threshold: float = DEFAULT_HIGH_THRESHOLD,
        severity_medium_threshold: float = DEFAULT_MEDIUM_THRESHOLD,
        allowed_rules_dirs: list[str] | None = None,
    ):
        if not NOVA_AVAILABLE:
            raise ImportError("nova-hunting not installed")  # pragma: no cover

        # Validate severity thresholds
        severity_high_threshold = _ensure_threshold(
            "severity_high_threshold", severity_high_threshold
        )
        severity_medium_threshold = _ensure_threshold(
            "severity_medium_threshold", severity_medium_threshold
        )
        if severity_medium_threshold > severity_high_threshold:
            raise ValueError(
                f"Invalid severity thresholds: medium ({severity_medium_threshold}) must be <= "
                f"high ({severity_high_threshold}) and both must be in [0, 1]"
            )

        self.enable_keywords = _ensure_bool("enable_keywords", enable_keywords)
        self.enable_semantics = _ensure_bool("enable_semantics", enable_semantics)
        self.enable_llm = _ensure_bool("enable_llm", enable_llm)
        self.severity_high_threshold = severity_high_threshold
        self.severity_medium_threshold = severity_medium_threshold

        # Define allowed directories for rules files
        # Default allows bundled rules directory and optionally user-specified directories
        self._allowed_rules_dirs: list[Path] = []
        if allowed_rules_dirs:
            for d in allowed_rules_dirs:
                resolved = Path(d).resolve()
                if resolved.is_dir():
                    self._allowed_rules_dirs.append(resolved)

        # Always allow the bundled rules directory
        bundled_dir = _BUNDLED_RULES_PATH.parent.resolve()
        if bundled_dir not in self._allowed_rules_dirs:
            self._allowed_rules_dirs.append(bundled_dir)

        # Load NOVA rules
        if rules_path:
            # Security: Validate rules_path to prevent path traversal
            self._validate_and_load_rules_path(rules_path)
        else:
            self.rules = self._load_bundled_rules()
        self.matchers: list = []
        if self.rules:
            # NovaMatcher prints diagnostics to stdout on construction, which would
            # corrupt machine-readable (--json) output. Suppress it.
            with contextlib.redirect_stdout(io.StringIO()):
                for rule in self.rules:
                    self.matchers.append(
                        NovaMatcher(rule=rule, create_llm_evaluator=self.enable_llm)
                    )
        else:
            logger.warning(
                "Nova-tracer enabled but no rules were loaded. "
                "Provide rules_path to activate semantic/LLM scanning."
            )

    def _validate_and_load_rules_path(self, rules_path: str) -> None:
        """
        Validate rules_path for security and load rules.

        Security checks:
        - Prevent path traversal via '..' or absolute paths outside allowed dirs
        - Resolve symlinks to their canonical paths
        - Ensure path points to a file (not directory)
        - Check file exists before opening

        Args:
            rules_path: Path to rules file

        Raises:
            ValueError: If path is invalid or outside allowed directories
            FileNotFoundError: If rules file doesn't exist
        """
        raw_path = Path(rules_path)
        resolved_path = raw_path.resolve()
        reject_unsafe_rules_path(rules_path)

        # Check if resolved path is within allowed directories
        if not self._is_path_allowed(resolved_path):
            raise ValueError(
                f"Rules path must be within allowed directories. "
                f"Resolved path '{resolved_path}' is outside permitted locations. "
                f"Allowed directories: {[str(d) for d in self._allowed_rules_dirs]}"
            )

        # Check for symlinks that resolve outside allowed directories (already done above)
        # but also log if it's a symlink for transparency
        if raw_path.is_symlink():
            logger.debug(
                "Rules path '%s' is a symlink resolving to '%s'",
                rules_path,
                resolved_path,
            )

        parser = NovaParser()

        try:
            file_content = read_rules_file_atomically(resolved_path)
            self.rules = parse_rule_content(file_content, parser)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Rules file not found: {rules_path}") from exc
        except IsADirectoryError as exc:
            raise ValueError(f"rules_path must be a file, not a directory: {rules_path}") from exc
        except PermissionError as exc:
            raise ValueError(f"Permission denied reading rules file: {rules_path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise ValueError(f"Rules file must be UTF-8 encoded: {exc}") from exc
        except OSError as exc:
            raise ValueError(f"Cannot read rules file: {exc}") from exc
        except Exception as exc:
            # BUG FIX: Catch all parsing exceptions, not just UnicodeDecodeError
            # parser.parse() can raise SyntaxError, ValueError, TypeError, etc.
            raise ValueError(f"Failed to parse rules file: {exc}") from exc

    def _is_path_allowed(self, resolved_path: Path) -> bool:
        """
        Check if a resolved path is within allowed directories.

        Args:
            resolved_path: Canonical path to check

        Returns:
            True if path is within an allowed directory, False otherwise
        """
        try:
            for allowed_dir in self._allowed_rules_dirs:
                try:
                    if resolved_path.is_relative_to(allowed_dir):
                        return True
                except (OSError, ValueError) as e:
                    # BUG FIX: Use WARNING level for visibility in production logs
                    # and include both path and allowed_dir for debugging context
                    logger.warning(
                        "Path permission check failed for %s against %s: %s",
                        resolved_path,
                        allowed_dir,
                        e,
                    )
                    continue
        except Exception as e:  # noqa: BLE001 - permission checks fail closed
            logger.warning("Unexpected error checking path permissions: %s", e)
        return False  # Default to deny on error

    def _load_bundled_rules(self):
        """Load bundled NOVA rules for prompt injection detection."""
        if not _BUNDLED_RULES_PATH.exists():
            return []
        parser = NovaParser()
        with open(_BUNDLED_RULES_PATH, encoding="utf-8") as f:
            content = f.read()
        rules, parse_failures = parse_bundled_rule_content(content, parser)
        for failure in parse_failures:
            logger.warning("Failed to parse bundled rule %r: %s", failure.preview, failure.error)
        if parse_failures:
            logger.warning(
                "Nova rules loaded: %d successful, %d failed to parse",
                len(rules),
                len(parse_failures),
            )
        return rules

    def scan(self, text: str) -> dict:
        """
        Scan text for prompt injection attempts.

        Args:
            text: Text to scan

        Returns:
            dict with score, severity, matched_rules, categories, scan_time_ms.
            When no rules are configured, returns a "disabled" result with
            score=null to indicate the scanner is inactive.
        """
        import time

        start = time.time()

        if not self.matchers:
            return {
                "score": None,  # Explicitly None to indicate disabled state
                "severity": "disabled",
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
                rule_name = str(result.get("rule_name", "unknown"))
                # Use confidence score if available, otherwise default to 0.5 (medium)
                # This provides graduated scoring while avoiding false positives
                # being treated as maximum severity
                if "confidence" in result:
                    score = _coerce_rule_score(result["confidence"], rule_name)
                elif "score" in result:
                    score = _coerce_rule_score(result["score"], rule_name)
                else:
                    # Log warning when score is missing, default to medium severity
                    logger.warning(
                        "Rule '%s' matched without confidence/score, defaulting to 0.5",
                        rule_name,
                    )
                    score = 0.5
                scores.append(score)
                matched_rules.append(rule_name)
                meta = result.get("meta", {})
                if "category" in meta:
                    categories.append(meta["category"])
            else:
                scores.append(0.0)

        score = max(scores) if scores else 0.0
        scan_time_ms = (time.time() - start) * 1000

        return {
            "score": score,
            "severity": (
                "high"
                if score >= self.severity_high_threshold
                else "medium" if score >= self.severity_medium_threshold else "low"
            ),
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
