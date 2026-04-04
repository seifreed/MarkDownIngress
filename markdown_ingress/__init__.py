"""
MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine for LLM Pipelines
"""

from markdown_ingress.api import (
    generate_security_report,
    ingest,
    ingest_async,
    ingest_many,
    ingest_many_async,
    retry_ingest,
)
from markdown_ingress.adapters.extractors.comparison import compare_extractors
from markdown_ingress.application.batch import BatchProcessor
from markdown_ingress.config_models import DomainPolicy, IngestConfig, RenderConfig
from markdown_ingress.core.benchmark import Benchmark, BenchmarkResult
from markdown_ingress.core.cache import Cache, MemoryCache, SQLiteCache
from markdown_ingress.core.config import Config, ConfigLoader, load_config
from markdown_ingress.core.orchestrator import get_ingest_stats, reset_ingest_stats
from markdown_ingress.core.plugin import Plugin, PluginLoader
from markdown_ingress.core.policy import Policy, PolicyEngine
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.shared_results import BatchResult

__version__ = "0.8.0"
__all__ = [
    # Core API
    "ingest",
    "ingest_async",
    "ingest_many",
    "ingest_many_async",
    "retry_ingest",
    "generate_security_report",
    # Models
    "SafeDocument",
    "SecurityReport",
    # Configuration dataclasses (v0.8.0)
    "IngestConfig",
    "RenderConfig",
    "DomainPolicy",
    # Batch processing
    "BatchProcessor",
    "BatchResult",
    # Caching
    "Cache",
    "MemoryCache",
    "SQLiteCache",
    "get_ingest_stats",
    "reset_ingest_stats",
    # Policy engine
    "Policy",
    "PolicyEngine",
    # Configuration (v0.4)
    "Config",
    "ConfigLoader",
    "load_config",
    # Plugins (v0.4)
    "Plugin",
    "PluginLoader",
    # Benchmarking (v0.4)
    "Benchmark",
    "BenchmarkResult",
    "compare_extractors",
]
