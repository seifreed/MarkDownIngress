"""Compatibility wrappers for application-layer exception transfer helpers."""

from __future__ import annotations

from markdown_ingress.core.exception_copy import (
    copy_exception_for_transfer as _copy_batch_exception,
)
from markdown_ingress.core.exception_copy import make_picklable as _make_picklable

__all__ = ["_copy_batch_exception", "_make_picklable"]
