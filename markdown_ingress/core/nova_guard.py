"""
Nova-tracer integration for advanced prompt injection detection.

This integration is optional and degrades safely when NOVA rules are not configured.
"""

import contextlib
import io
import logging
import time
from pathlib import Path
from typing import Any, TypedDict, Unpack, cast

from markdown_ingress.config_validation import collect_option_values
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
from markdown_ingress.runtime_helpers import is_dependency_available, load_optional_module

logger = logging.getLogger(__name__)

NovaMatcher: Any = None
NovaParser: Any = None
NOVA_AVAILABLE = is_dependency_available("nova")

_BUNDLED_RULES_PATH = Path(__file__).parent.parent / "rules" / "prompt_injection.nova"

# Default severity thresholds (can be overridden via constructor)
DEFAULT_HIGH_THRESHOLD = 0.7
DEFAULT_MEDIUM_THRESHOLD = 0.3
_PATH_PERMISSION_ERRORS: tuple[type[Exception], ...] = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class NovaGuardOptions(TypedDict, total=False):
    enable_keywords: bool
    enable_semantics: bool
    enable_llm: bool
    rules_path: str | None
    severity_high_threshold: float
    severity_medium_threshold: float
    allowed_rules_dirs: list[str] | None


_NOVA_GUARD_POSITIONAL_OPTION_NAMES = (
    "enable_keywords",
    "enable_semantics",
    "enable_llm",
    "rules_path",
)
_NOVA_GUARD_OPTION_NAME_SET = frozenset(NovaGuardOptions.__annotations__)


def _normalize_nova_guard_options(
    args: tuple[object, ...],
    options: NovaGuardOptions,
) -> NovaGuardOptions:
    return cast(
        NovaGuardOptions,
        collect_option_values(
            "NovaGuard()",
            _NOVA_GUARD_POSITIONAL_OPTION_NAMES,
            args,
            options,
            valid_option_names=_NOVA_GUARD_OPTION_NAME_SET,
            too_many_positional_message=(
                f"NovaGuard() takes at most 4 positional arguments ({len(args)} given)"
            ),
        ),
    )


def _load_nova_api() -> tuple[Any, Any]:
    """Load Nova's heavy optional API only when advanced scanning is requested."""
    global NovaMatcher, NovaParser

    if NovaMatcher is not None and NovaParser is not None:
        return NovaMatcher, NovaParser

    try:
        nova_module = load_optional_module("nova", purpose="nova-hunting integration")
    except ImportError as exc:
        raise ImportError("nova-hunting not installed") from exc

    NovaMatcher = getattr(nova_module, "NovaMatcher")
    NovaParser = getattr(nova_module, "NovaParser")
    return NovaMatcher, NovaParser


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


def _collect_allowed_rules_dirs(allowed_rules_dirs: list[str] | None) -> list[Path]:
    collected: list[Path] = []
    if allowed_rules_dirs:
        for raw_dir in allowed_rules_dirs:
            resolved = Path(raw_dir).resolve()
            if resolved.is_dir():
                collected.append(resolved)
    bundled_dir = _BUNDLED_RULES_PATH.parent.resolve()
    if bundled_dir not in collected:
        collected.append(bundled_dir)
    return collected


def _build_disabled_scan_result() -> dict[str, Any]:
    return {
        "score": None,
        "severity": "disabled",
        "matched_rules": [],
        "categories": [],
        "scan_time_ms": 0.0,
        "rules_loaded": 0,
        "disabled_reason": "no_rules_configured",
        "tiers_used": {"keywords": False, "semantics": False, "llm": False},
    }


def _build_matcher_score(result: dict[str, Any], rule_name: str) -> float:
    if "confidence" in result:
        return _coerce_rule_score(result["confidence"], rule_name)
    if "score" in result:
        return _coerce_rule_score(result["score"], rule_name)
    logger.warning(
        "Rule '%s' matched without confidence/score, defaulting to 0.5",
        rule_name,
    )
    return 0.5


def _run_matchers(matchers: list[Any], text: str) -> tuple[list[float], list[str], list[Any]]:
    scores: list[float] = []
    matched_rules: list[str] = []
    categories: list[Any] = []

    for matcher in matchers:
        result = matcher.check_prompt(text)
        if not result.get("matched"):
            scores.append(0.0)
            continue

        rule_name = str(result.get("rule_name", "unknown"))
        scores.append(_build_matcher_score(result, rule_name))
        matched_rules.append(rule_name)
        category = result.get("meta", {}).get("category")
        if category is not None:
            categories.append(category)

    return scores, matched_rules, categories


def _compute_nova_severity(score: float, *, high: float, medium: float) -> str:
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


def _build_scan_response(
    score: float,
    matched_rules: list[str],
    categories: list[Any],
    scan_time_ms: float,
    rules_loaded: int,
    *,
    enable_keywords: bool,
    enable_semantics: bool,
    enable_llm: bool,
    high: float,
    medium: float,
) -> dict[str, Any]:
    return {
        "score": score,
        "severity": _compute_nova_severity(score, high=high, medium=medium),
        "matched_rules": matched_rules,
        "categories": categories,
        "scan_time_ms": scan_time_ms,
        "rules_loaded": rules_loaded,
        "tiers_used": {
            "keywords": enable_keywords,
            "semantics": enable_semantics,
            "llm": enable_llm,
        },
    }


def _build_matchers_from_rules(
    nova_matcher: Any,
    rules: list[Any],
    *,
    create_llm_evaluator: bool,
) -> list[Any]:
    if not rules:
        return []

    with contextlib.redirect_stdout(io.StringIO()):
        return [
            nova_matcher(rule=rule, create_llm_evaluator=create_llm_evaluator) for rule in rules
        ]


class NovaGuard:
    """Advanced prompt injection detection using Nova Framework."""

    def __init__(self, *args: object, **options: Unpack[NovaGuardOptions]) -> None:
        parsed = _normalize_nova_guard_options(args, options)
        enable_keywords = parsed.get("enable_keywords", True)
        enable_semantics = parsed.get("enable_semantics", True)
        enable_llm = parsed.get("enable_llm", False)
        rules_path = parsed.get("rules_path")
        severity_high_threshold = parsed.get("severity_high_threshold", DEFAULT_HIGH_THRESHOLD)
        severity_medium_threshold = parsed.get(
            "severity_medium_threshold",
            DEFAULT_MEDIUM_THRESHOLD,
        )
        allowed_rules_dirs = parsed.get("allowed_rules_dirs")

        if not NOVA_AVAILABLE:
            raise ImportError("nova-hunting not installed")  # pragma: no cover
        nova_matcher, nova_parser = _load_nova_api()
        self._nova_matcher = nova_matcher
        self._nova_parser = nova_parser

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
        self._allowed_rules_dirs = _collect_allowed_rules_dirs(allowed_rules_dirs)

        # Load NOVA rules
        self.rules = self._load_rules(rules_path)
        self.matchers = _build_matchers_from_rules(
            self._nova_matcher,
            self.rules,
            create_llm_evaluator=self.enable_llm,
        )
        if not self.matchers:
            logger.warning(
                "Nova-tracer enabled but no rules were loaded. "
                "Provide rules_path to activate semantic/LLM scanning."
            )

    def _load_rules(self, rules_path: str | None) -> list[Any]:
        if rules_path:
            return self._load_rules_from_path(rules_path)
        return self._load_bundled_rules()

    def _validate_and_load_rules_path(self, rules_path: str) -> list[Any]:
        """Compatibility wrapper retained for existing internal callers/tests."""
        return self._load_rules_from_path(rules_path)

    def _load_rules_from_path(self, rules_path: str) -> list[Any]:
        """
        Validate rules_path for security and load rules.

        Security checks:
        - Prevent path traversal via '..' or absolute paths outside allowed dirs
        - Resolve symlinks to their canonical paths
        - Ensure path points to a file (not directory)
        - Check file exists before opening

        Args:
        """
        raw_path = Path(rules_path)
        resolved_path = raw_path.resolve()
        reject_unsafe_rules_path(rules_path)

        if not self._is_path_allowed(resolved_path):
            raise ValueError(
                f"Rules path must be within allowed directories. "
                f"Resolved path '{resolved_path}' is outside permitted locations. "
                f"Allowed directories: {[str(d) for d in self._allowed_rules_dirs]}"
            )

        if raw_path.is_symlink():
            logger.debug(
                "Rules path '%s' is a symlink resolving to '%s'",
                rules_path,
                resolved_path,
            )

        parser_factory = getattr(self, "_nova_parser", NovaParser)
        parser = parser_factory()

        try:
            file_content = read_rules_file_atomically(resolved_path)
            return parse_rule_content(file_content, parser)
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
                    logger.warning(
                        "Path permission check failed for %s against %s: %s",
                        resolved_path,
                        allowed_dir,
                        e,
                    )
                    continue
        except _PATH_PERMISSION_ERRORS as e:
            logger.warning("Unexpected error checking path permissions: %s", e)
        return False  # Default to deny on error

    def _load_bundled_rules(self) -> list[Any]:
        """Load bundled NOVA rules for prompt injection detection."""
        if not _BUNDLED_RULES_PATH.exists():
            return []
        parser_factory = getattr(self, "_nova_parser", NovaParser)
        parser = parser_factory()
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
        start = time.time()

        if not self.matchers:
            return _build_disabled_scan_result()

        scores, matched_rules, categories = _run_matchers(self.matchers, text)
        score = max(scores) if scores else 0.0
        scan_time_ms = (time.time() - start) * 1000

        return _build_scan_response(
            score,
            matched_rules,
            categories,
            scan_time_ms,
            len(self.rules),
            enable_keywords=self.enable_keywords,
            enable_semantics=self.enable_semantics,
            enable_llm=self.enable_llm,
            high=self.severity_high_threshold,
            medium=self.severity_medium_threshold,
        )

    @staticmethod
    def is_available() -> bool:
        """Check if nova-hunting is installed."""
        return NOVA_AVAILABLE
