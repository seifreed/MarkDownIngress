"""
MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine for LLM Pipelines
"""

from markdown_ingress.api import ingest
from markdown_ingress.models import SafeDocument

__version__ = "0.1.0"
__all__ = ["ingest", "SafeDocument"]
