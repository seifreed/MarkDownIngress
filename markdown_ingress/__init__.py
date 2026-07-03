"""
MarkDownIngress - Deterministic, Injection-Resistant Web -> Markdown Engine for LLM Pipelines
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "1.0.0"

_EXPORTS = {
    "BatchProcessor": "markdown_ingress.application.batch",
    "BatchResult": "markdown_ingress.shared_results",
    "Benchmark": "markdown_ingress.core.benchmark",
    "BenchmarkResult": "markdown_ingress.core.benchmark",
    "Cache": "markdown_ingress.core.cache",
    "Config": "markdown_ingress.core.config",
    "ConfigLoader": "markdown_ingress.core.config",
    "DomainPolicy": "markdown_ingress.config_models",
    "IngestConfig": "markdown_ingress.config_models",
    "MemoryCache": "markdown_ingress.adapters.cache.memory",
    "Plugin": "markdown_ingress.core.plugin",
    "PluginLoader": "markdown_ingress.core.plugin",
    "Policy": "markdown_ingress.core.policy",
    "PolicyEngine": "markdown_ingress.core.policy",
    "RenderConfig": "markdown_ingress.config_models",
    "SQLiteCache": "markdown_ingress.adapters.cache.sqlite",
    "SafeDocument": "markdown_ingress.models",
    "SecurityReport": "markdown_ingress.models",
    "compare_extractors": "markdown_ingress.api",
    "generate_security_report": "markdown_ingress.api",
    "get_ingest_stats": "markdown_ingress.core.orchestrator",
    "ingest": "markdown_ingress.api",
    "ingest_async": "markdown_ingress.api",
    "ingest_many": "markdown_ingress.api",
    "ingest_many_async": "markdown_ingress.api",
    "load_config": "markdown_ingress.core.config",
    "reset_ingest_stats": "markdown_ingress.core.orchestrator",
    "retry_ingest": "markdown_ingress.api",
}

__all__ = sorted([*_EXPORTS, "__version__"])


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in {"Config", "ConfigLoader", "load_config"}:
        from markdown_ingress.application.bootstrap import register_all_factories

        register_all_factories()
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
