"""Bootstrap: register concrete adapter implementations into core registries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from markdown_ingress.config_models import IngestConfig, RenderConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.config import register_cache_factory
from markdown_ingress.core.interfaces import IFetcher, IRenderer


def _default_cache_factory(cache_type: str, sqlite_path: str | None, ttl: int) -> Cache:
    from markdown_ingress.adapters.cache.memory import MemoryCache
    from markdown_ingress.adapters.cache.sqlite import SQLiteCache

    if cache_type == "sqlite":
        return SQLiteCache(db_path=sqlite_path or ".cache/markdown_ingress.db", default_ttl=ttl)
    return MemoryCache(default_ttl=ttl)


def default_fetcher_factory(config: IngestConfig) -> IFetcher:
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    return Fetcher(
        timeout=config.timeout,
        user_agent=getattr(config, "fetcher_user_agent", None),
        allow_local_urls=config.allow_local_urls,
        domain_request_interval=config.domain_request_interval,
        circuit_breaker_threshold=config.circuit_breaker_threshold,
        circuit_breaker_open_seconds=config.circuit_breaker_open_seconds,
    )


def default_benchmark_fetcher_factory() -> Callable[[], IFetcher]:
    """Return the benchmark fetcher constructor with default configuration."""

    def _factory() -> IFetcher:
        return default_fetcher_factory(IngestConfig())

    return _factory


def default_renderer_factory(config: RenderConfig) -> IRenderer:
    from markdown_ingress.adapters.rendering.playwright_renderer import PlaywrightRenderer

    return PlaywrightRenderer(config=config)


class _CompareExtractorsFn(Protocol):
    def __call__(self, html: str, *, model: str = "gpt-4") -> dict[str, dict[str, object]]: ...


def default_compare_extractors_factory() -> _CompareExtractorsFn:
    """Return the default extractor comparison function."""
    from markdown_ingress.adapters.extractors.comparison import compare_extractors

    return compare_extractors


def register_all_factories() -> None:
    """Register all concrete adapter implementations into core registries. Idempotent."""
    from markdown_ingress.adapters.extractors.readability_extractor import Extractor
    from markdown_ingress.adapters.markdown.markdownify_converter import MarkdownConverter
    from markdown_ingress.adapters.tokens.tiktoken_estimator import TokenEstimator
    from markdown_ingress.core.document_builder import register_document_builder_factories
    from markdown_ingress.core.structured import (
        register_token_estimator_factory as _register_token_estimator_factory,
    )

    register_document_builder_factories(
        extractor_factory=lambda strict: Extractor(strict=strict),
        md_converter_factory=MarkdownConverter,
        token_estimator_factory=lambda model: TokenEstimator(model=model),
    )
    _register_token_estimator_factory(TokenEstimator)
    register_cache_factory(_default_cache_factory)
