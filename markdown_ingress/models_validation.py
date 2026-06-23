"""Validation helpers for public data models."""

from __future__ import annotations

from datetime import datetime

from markdown_ingress.config_validation import ensure_finite_float as _ensure_finite_float_metric
from markdown_ingress.config_validation import ensure_int as _ensure_int_metric
from markdown_ingress.config_validation import ensure_str as _ensure_str


def _ensure_non_negative_int_metric(field_name: str, value: object) -> int:
    metric = _ensure_int_metric(field_name, value)
    if metric < 0:
        raise ValueError(f"{field_name} must be non-negative, got {metric}")
    return metric


def _ensure_iso_datetime_str(field_name: str, value: object) -> str:
    timestamp = _ensure_str(field_name, value)
    normalized = f"{timestamp[:-1]}+00:00" if timestamp.endswith("Z") else timestamp
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime, got {timestamp!r}") from exc
    return timestamp


def _ensure_optional_str(field_name: str, value: object | None) -> str | None:
    if value is None:
        return None
    return _ensure_str(field_name, value)


def _ensure_dict(field_name: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict, got {type(value).__name__}")
    return value


def _ensure_optional_dict(field_name: str, value: object | None) -> dict | None:
    if value is None:
        return None
    return _ensure_dict(field_name, value)


def _ensure_str_list(field_name: str, value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings, got {type(value).__name__}")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string, got {type(item).__name__}")
    return value


def _ensure_non_negative_int_list(field_name: str, value: object) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(
            f"{field_name} must be a list of non-negative ints, got {type(value).__name__}"
        )
    for index, item in enumerate(value):
        _ensure_non_negative_int_metric(f"{field_name}[{index}]", item)
    return value


def _ensure_dict_list(field_name: str, value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of dicts, got {type(value).__name__}")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{index}] must be a dict, got {type(item).__name__}")
    return value


def _ensure_optional_dict_list(field_name: str, value: object | None) -> list[dict] | None:
    if value is None:
        return None
    return _ensure_dict_list(field_name, value)


def _ensure_percentage(field_name: str, value: object) -> float:
    percentage = _ensure_finite_float_metric(field_name, value)
    if not 0.0 <= percentage <= 100.0:
        raise ValueError(f"{field_name} must be between 0.0 and 100.0, got {percentage}")
    return percentage
