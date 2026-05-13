"""SQLite cache path validation."""

from __future__ import annotations

import logging
from pathlib import Path

_logger = logging.getLogger(__name__)


def validate_db_path(db_path: str, allow_absolute_paths: bool = True) -> Path:
    """Validate and resolve the SQLite database path."""
    if not db_path or not db_path.strip():
        raise ValueError("db_path cannot be empty")

    path = Path(db_path)
    try:
        resolved_path = _resolve_candidate_path(path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid db_path '{db_path}': {exc}") from exc

    cwd = Path.cwd().resolve()
    try:
        resolved_path.relative_to(cwd)
    except ValueError:
        if path.is_absolute():
            if not allow_absolute_paths:
                raise ValueError(
                    f"Absolute db_path '{db_path}' not allowed. "
                    f"Resolved path: '{resolved_path}', Working directory: '{cwd}'. "
                    "Set allow_absolute_paths=True to permit absolute paths."
                ) from None
            _logger.warning(
                "SQLiteCache using absolute path '%s' outside working directory '%s'. "
                "Ensure this path is intentionally specified and secure.",
                resolved_path,
                cwd,
            )
        else:
            raise ValueError(
                f"db_path '{db_path}' resolves to path outside current working directory. "
                f"Resolved path: '{resolved_path}', Working directory: '{cwd}'. "
                "Path traversal is not allowed for security reasons."
            ) from None

    return resolved_path


def _resolve_candidate_path(path: Path) -> Path:
    if path.exists():
        return path.resolve()

    parent = path.parent
    if parent.exists():
        resolved_parent = parent.resolve()
    else:
        resolved_parent = (Path.cwd() / parent).resolve()
    return resolved_parent / path.name
