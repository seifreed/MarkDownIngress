"""Dependency resolution for document building."""

from __future__ import annotations

from collections.abc import Callable

from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.interfaces import IExtractor, IMarkdownConverter, ITokenEstimator
from markdown_ingress.core.scoring import Scorer

_extractor_factory: Callable[[bool], IExtractor] | None = None
_md_converter_factory: Callable[[], IMarkdownConverter] | None = None
_token_estimator_factory: Callable[[str], ITokenEstimator] | None = None


def register_document_builder_factories(
    extractor_factory: Callable[[bool], IExtractor],
    md_converter_factory: Callable[[], IMarkdownConverter],
    token_estimator_factory: Callable[[str], ITokenEstimator],
) -> None:
    global _extractor_factory, _md_converter_factory, _token_estimator_factory
    _extractor_factory = extractor_factory
    _md_converter_factory = md_converter_factory
    _token_estimator_factory = token_estimator_factory


def resolve_pipeline_dependencies(orchestrator, config: IngestConfig):
    """Return (extractor, md_converter, hasher, token_estimator, scorer)."""
    if orchestrator.extractor is None:
        if _extractor_factory is None:
            raise RuntimeError(
                "No extractor factory registered — call register_document_builder_factories()."
            )
        extractor: IExtractor = _extractor_factory(config.strict)
    else:
        extractor = orchestrator.extractor

    if orchestrator.md_converter is None:
        if _md_converter_factory is None:
            raise RuntimeError("No md_converter factory registered.")
        md_converter: IMarkdownConverter = _md_converter_factory()
    else:
        md_converter = orchestrator.md_converter

    hasher = orchestrator.hasher or Hasher()

    if orchestrator.token_estimator is None:
        if _token_estimator_factory is None:
            raise RuntimeError("No token_estimator factory registered.")
        token_estimator: ITokenEstimator = _token_estimator_factory(config.model)
    else:
        token_estimator = orchestrator.token_estimator

    scorer = orchestrator.scorer or Scorer()
    return extractor, md_converter, hasher, token_estimator, scorer
