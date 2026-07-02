"""Shared helpers for restoring invalid environment overrides."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldRestoreContext:
    """State required to restore fields invalidated by environment overrides."""

    config: Any
    previous_values: dict[str, Any]
    previous_explicit: dict[str, bool]
    explicit: set[str]


def restore_field(
    context: FieldRestoreContext,
    attr_name: str,
    message: str,
    *args: object,
) -> None:
    """Revert a field to its pre-env-override value and update explicit tracking."""
    _logger.warning(message, *args)
    setattr(context.config, attr_name, context.previous_values[attr_name])
    if context.previous_explicit.get(attr_name, False):
        context.explicit.add(attr_name)
    else:
        context.explicit.discard(attr_name)
