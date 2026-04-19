"""Bootstrap: register concrete adapter implementations into core registries."""

from markdown_ingress.adapters.cache.memory import MemoryCache
from markdown_ingress.adapters.cache.sqlite import SQLiteCache
from markdown_ingress.adapters.extractors.readability_extractor import Extractor
from markdown_ingress.adapters.markdown.markdownify_converter import MarkdownConverter
from markdown_ingress.adapters.tokens.tiktoken_estimator import TokenEstimator
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.config import register_cache_factory
from markdown_ingress.core.document_builder import register_document_builder_factories
from markdown_ingress.core.structured import register_token_estimator_factory


def _default_cache_factory(cache_type: str, sqlite_path: str | None, ttl: int) -> Cache:
    if cache_type == "sqlite":
        return SQLiteCache(db_path=sqlite_path, default_ttl=ttl)
    return MemoryCache(default_ttl=ttl)


def register_all_factories() -> None:
    """Register all concrete adapter implementations into core registries. Idempotent."""
    register_document_builder_factories(
        extractor_factory=lambda strict: Extractor(strict=strict),
        md_converter_factory=MarkdownConverter,
        token_estimator_factory=lambda model: TokenEstimator(model=model),
    )
    register_token_estimator_factory(TokenEstimator)
    register_cache_factory(_default_cache_factory)
