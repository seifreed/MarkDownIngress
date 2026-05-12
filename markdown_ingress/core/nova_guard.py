"""
Nova-tracer integration for advanced prompt injection detection.

This integration is optional and degrades safely when NOVA rules are not configured.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from nova import NovaMatcher, NovaParser  # type: ignore[import-untyped]

    NOVA_AVAILABLE = True
except ImportError:  # pragma: no cover
    NOVA_AVAILABLE = False  # pragma: no cover

_BUNDLED_RULES_PATH = Path(__file__).parent.parent / "rules" / "prompt_injection.nova"

# Default severity thresholds (can be overridden via constructor)
DEFAULT_HIGH_THRESHOLD = 0.7
DEFAULT_MEDIUM_THRESHOLD = 0.3


class NovaGuard:
    """Advanced prompt injection detection using Nova Framework."""

    def __init__(
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
        if not (0 <= severity_medium_threshold <= severity_high_threshold <= 1):
            raise ValueError(
                f"Invalid severity thresholds: medium ({severity_medium_threshold}) must be <= "
                f"high ({severity_high_threshold}) and both must be in [0, 1]"
            )

        self.enable_keywords = enable_keywords
        self.enable_semantics = enable_semantics
        self.enable_llm = enable_llm
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
            for rule in self.rules:
                self.matchers.append(NovaMatcher(rule=rule, create_llm_evaluator=enable_llm))
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

        # Check for path traversal sequences (including URL-encoded variants)
        # BUG FIX: Previous check only looked for literal "..", missing encoded variants
        import urllib.parse

        # Security: Check for null bytes (prevent null byte injection)
        if "\x00" in str(rules_path) or "%00" in str(rules_path).lower():
            raise ValueError(f"Null bytes not allowed in rules_path: {rules_path}")

        def _decode_fully(encoded_path: str) -> str:
            """Decode URL-encoded path until fully decoded."""
            decoded = encoded_path
            for _ in range(
                25
            ):  # Fixed-point loop breaks early; cap prevents adversarial deep-encoding
                new_decoded = urllib.parse.unquote(decoded)
                if new_decoded == decoded:
                    break
                decoded = new_decoded
            return decoded

        raw_lower = str(rules_path).lower().replace("\\", "/")
        fully_decoded = _decode_fully(str(rules_path))
        normalized_lower = fully_decoded.lower().replace("\\", "/")

        raw_encoded_traversal_patterns = [
            "%2e%2e",
            "%252e",
            "%5c",
            "%c0%ae",
            "%c1%9c",
            "%c0%af",
            "%c0%2f",
            "..%00",
            "..%252f",
            "..%255c",
            "%e0%80%ae",
            "..%c0%ae/",
        ]
        if any(pattern in raw_lower for pattern in raw_encoded_traversal_patterns):
            raise ValueError(f"Path traversal not allowed in rules_path: {rules_path}")
        if ".." in normalized_lower or "..;/" in normalized_lower:
            raise ValueError(f"Path traversal not allowed in rules_path: {rules_path}")

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

        # Open first, then fstat() on the open fd — truly atomic size check.
        # stat() + open() had a TOCTOU window where a file could be swapped between calls.
        max_rules_file_size = 10 * 1024 * 1024  # 10MB
        parser = NovaParser()

        try:
            import os

            with resolved_path.open("r", encoding="utf-8") as f:
                file_size = os.fstat(f.fileno()).st_size
                if file_size > max_rules_file_size:
                    raise ValueError(
                        f"Rules file too large: {file_size} bytes (max {max_rules_file_size} bytes). "
                        "Large files may indicate a DoS attempt."
                    )
                file_content = f.read()
            blocks = self._extract_rule_blocks(file_content)
            if blocks:
                self.rules = []
                for block in blocks:
                    self.rules.append(parser.parse(block.strip()))
            else:
                self.rules = [parser.parse(file_content)]
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
        except Exception as e:
            logger.warning("Unexpected error checking path permissions: %s", e)
        return False  # Default to deny on error

    def _load_bundled_rules(self):
        """Load bundled NOVA rules for prompt injection detection."""
        if not _BUNDLED_RULES_PATH.exists():
            return []
        parser = NovaParser()
        rules = []
        parse_failures = []
        with open(_BUNDLED_RULES_PATH, encoding="utf-8") as f:
            content = f.read()
        # Split rule blocks using a linear-time brace-depth scanner
        # instead of a regex with nested quantifiers (avoids ReDoS).
        for block in self._extract_rule_blocks(content):
            try:
                rules.append(parser.parse(block.strip()))
            except Exception as e:
                logger.warning("Failed to parse bundled rule: %s", e)
                parse_failures.append((block[:50], str(e)))
        if parse_failures:
            logger.warning(
                "Nova rules loaded: %d successful, %d failed to parse",
                len(rules),
                len(parse_failures),
            )
        return rules

    @staticmethod
    def _extract_rule_blocks(content: str) -> list[str]:
        """Extract top-level ``rule <name> { ... }`` blocks in O(n) time."""
        import re

        blocks: list[str] = []
        # Find each "rule <word> {" header; then scan braces at O(n).
        for m in re.finditer(r"rule\s+\w+\s*\{", content):
            start = m.start()
            depth = 1
            pos = m.end()
            while pos < len(content) and depth > 0:
                ch = content[pos]
                if ch == '"' or ch == "'":
                    quote = ch
                    pos += 1
                    while pos < len(content) and content[pos] != quote:
                        if content[pos] == "\\":
                            pos += 1  # skip escaped char
                            if pos >= len(content):
                                break
                        pos += 1
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                pos += 1
            if depth == 0:
                blocks.append(content[start:pos])
        return blocks

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
                # Use confidence score if available, otherwise default to 0.5 (medium)
                # This provides graduated scoring while avoiding false positives
                # being treated as maximum severity
                if "confidence" in result:
                    score = result["confidence"]
                elif "score" in result:
                    score = result["score"]
                else:
                    # Log warning when score is missing, default to medium severity
                    logger.warning(
                        "Rule '%s' matched without confidence/score, defaulting to 0.5",
                        result.get("rule_name", "unknown"),
                    )
                    score = 0.5
                scores.append(score)
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
