"""
MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine for LLM Pipelines
"""

from markdown_ingress.api import ingest, generate_security_report
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.core.batch import BatchProcessor, BatchResult
from markdown_ingress.core.cache import Cache, MemoryCache, SQLiteCache
from markdown_ingress.core.policy import Policy, PolicyEngine
from markdown_ingress.core.config import Config, ConfigLoader, load_config
from markdown_ingress.core.plugin import Plugin, PluginLoader
from markdown_ingress.core.benchmark import Benchmark, BenchmarkResult

__version__ = "0.4.0"
__all__ = [
    # Core API
    "ingest",
    "generate_security_report",
    
    # Models
    "SafeDocument",
    "SecurityReport",
    
    # Batch processing
    "BatchProcessor",
    "BatchResult",
    
    # Caching
    "Cache",
    "MemoryCache",
    "SQLiteCache",
    
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
]
