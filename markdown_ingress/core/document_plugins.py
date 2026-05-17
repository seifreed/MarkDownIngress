"""Plugin lifecycle helpers for document ingestion."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from markdown_ingress.core.plugin import PluginLoader
from markdown_ingress.models import FetchResult, SafeDocument

_logger = logging.getLogger(__name__)


@dataclass
class DocumentPluginContext:
    """Mutable plugin state for a single document build."""

    extra_patterns: list[str | tuple[str, float]] = field(default_factory=list)
    loader: PluginLoader | None = None
    plugins_loaded: int = 0


def create_document_plugin_context(
    custom_patterns: Iterable[str | tuple[str, float]],
) -> DocumentPluginContext:
    """Create plugin processing state seeded with configured custom patterns."""
    return DocumentPluginContext(extra_patterns=list(custom_patterns))


def load_document_plugins(context: DocumentPluginContext, plugin_dirs: Iterable[str]) -> None:
    """Load configured plugins and append their patterns to the context."""
    plugin_dirs = list(plugin_dirs)
    if not plugin_dirs:
        return
    context.loader = PluginLoader()
    for plugin_dir in plugin_dirs:
        context.plugins_loaded += context.loader.load_from_directory(plugin_dir)
    context.extra_patterns.extend(context.loader.get_all_patterns())


def unload_document_plugins(
    plugin_loader: PluginLoader | None,
    document: SafeDocument | None,
    fetch_result: FetchResult,
) -> None:
    """Unload plugins and attach any unload errors to the document or fetch metadata."""
    if plugin_loader is None:
        return
    unload_errors: list[str] = []
    for plugin_name in list(plugin_loader.plugins):
        try:
            plugin_loader.unload_plugin(plugin_name)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # pragma: no cover - defensive logging path
            unload_errors.append(f"{plugin_name}: {type(exc).__name__}: {exc}")
    if unload_errors:
        for message in unload_errors:
            _logger.error("Plugin unload failed after request completion: %s", message)
        if document is not None:
            document.metadata.setdefault("warnings", []).extend(unload_errors)
        else:
            fetch_result.metadata.setdefault("warnings", []).extend(unload_errors)
