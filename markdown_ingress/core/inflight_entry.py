"""In-flight request entry model and follower notification helpers."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from markdown_ingress.models import SafeDocument


@dataclass
class InFlightEntry:
    """Track an in-progress ingestion so duplicate callers can await the same result."""

    condition: threading.Condition = field(default_factory=threading.Condition)
    followers: int = 0
    done: bool = False
    completing: bool = False
    leader_active: bool = True
    document: SafeDocument | None = None
    error: Exception | None = None
    created_at: float = field(default_factory=time.monotonic)
    request_key: str = ""


def notify_entries_inactive(entries: Iterable[InFlightEntry]) -> None:
    """Mark entries inactive and wake every follower waiting on their condition."""
    for entry in entries:
        with entry.condition:
            entry.leader_active = False
            entry.condition.notify_all()
