"""Regression tests for immutable class-level constants."""

import pytest

from markdown_ingress.adapters.extractors.readability_extractor import Extractor
from markdown_ingress.adapters.normalizing.normalizer import Normalizer
from markdown_ingress.adapters.rendering.playwright_renderer import Renderer
from markdown_ingress.adapters.tokens.tiktoken_estimator import TokenEstimator
from markdown_ingress.core.config_loader import ConfigLoader
from markdown_ingress.core.security import SecurityAnalyzer
from markdown_ingress.core.structured import HTMLStructureExtractor


def test_class_level_constants_are_not_mutable_shared_collections():
    assert isinstance(Extractor.REMOVE_TAGS, tuple)
    assert isinstance(Normalizer.ZERO_WIDTH_CHARS, tuple)
    assert isinstance(Normalizer.TRACKING_PARAMS, frozenset)
    assert isinstance(Renderer.LOAD_STRATEGIES, tuple)
    assert isinstance(Renderer.CONTENT_SELECTORS, tuple)
    assert isinstance(ConfigLoader.DEFAULT_LOCATIONS, tuple)
    assert isinstance(SecurityAnalyzer.IMPERATIVE_VERBS, frozenset)
    assert isinstance(HTMLStructureExtractor.CONTAINER_TAGS, frozenset)

    with pytest.raises(TypeError):
        TokenEstimator.MODEL_ENCODINGS["new-model"] = "cl100k_base"

    with pytest.raises(TypeError):
        HTMLStructureExtractor.BLOCK_TAGS["section"] = ("section", None)
