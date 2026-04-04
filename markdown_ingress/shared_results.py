"""Shared result DTOs used across layers."""

from __future__ import annotations

from dataclasses import dataclass, field

from markdown_ingress.models import SafeDocument


@dataclass
class BatchErrorItem:
    """A batch failure bound to the original input position."""

    index: int
    url: str
    error: str


@dataclass
class BatchResult:
    """Result from batch processing."""

    total: int
    successful: int
    failed: int
    documents: list[SafeDocument | None] = field(default_factory=list)
    errors: list[BatchErrorItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalize legacy dict-style errors into the positional error list contract."""
        if isinstance(self.errors, dict):
            self.errors = [
                BatchErrorItem(index=i, url=url, error=error)
                for i, (url, error) in enumerate(self.errors.items())
            ]
        normalized: list[BatchErrorItem] = []
        for item in self.errors:
            if isinstance(item, BatchErrorItem):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(
                    BatchErrorItem(
                        index=int(item.get("index", -1)),
                        url=str(item.get("url", "")),
                        error=str(item.get("error", "")),
                    )
                )
        self.errors = normalized

    @property
    def error_items(self) -> list[BatchErrorItem]:
        """Expose the positional batch errors explicitly."""
        return list(self.errors)

    @property
    def errors_by_url(self) -> dict[str, list[str]]:
        """Group errors by URL without dropping duplicate failures."""
        grouped: dict[str, list[str]] = {}
        for item in self.errors:
            grouped.setdefault(item.url, []).append(item.error)
        return grouped

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        return (self.successful / self.total * 100) if self.total > 0 else 0.0
