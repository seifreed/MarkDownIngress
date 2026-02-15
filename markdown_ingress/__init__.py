"""
MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine for LLM Pipelines
"""

from markdown_ingress.api import ingest
from markdown_ingress.models import SafeDocument
from markdown_ingress.core.batch import BatchProcessor, BatchResult
from markdown_ingress.core.cache import Cache, MemoryCache, SQLiteCache
from markdown_ingress.core.policy import Policy, PolicyEngine

__version__ = "0.3.0"
__all__ = [
    "ingest",
    "SafeDocument",
    "BatchProcessor",
    "BatchResult",
    "Cache",
    "MemoryCache",
    "SQLiteCache",
    "Policy",
    "PolicyEngine",
]
