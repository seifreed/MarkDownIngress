"""Regression tests for bootstrap factory wiring."""

from __future__ import annotations

from markdown_ingress import api as api_module
from markdown_ingress.application import bootstrap as bootstrap_module
from markdown_ingress.application import use_cases
from markdown_ingress.config_models import IngestConfig, RenderConfig


def test_api_benchmark_fetcher_factory_uses_default_bootstrap_factory(monkeypatch) -> None:
    calls: list[IngestConfig] = []

    class FakeFetcher:
        pass

    def fake_default_fetcher_factory(config: IngestConfig) -> object:
        calls.append(config)
        return FakeFetcher()

    monkeypatch.setattr(bootstrap_module, "default_fetcher_factory", fake_default_fetcher_factory)

    fetcher_factory = api_module.benchmark_fetcher_factory()
    created = fetcher_factory()

    assert isinstance(created, FakeFetcher)
    assert len(calls) == 1
    assert calls[0] == IngestConfig()


def test_api_compare_extractors_uses_configurable_factory(monkeypatch) -> None:
    recorded: list[tuple[str, str]] = []

    def fake_compare_extractors(html: str, *, model: str = "gpt-4") -> dict[str, dict[str, object]]:
        recorded.append((html, model))
        return {"fake": {"available": True, "model": model}}

    monkeypatch.setattr(
        api_module,
        "default_compare_extractors_factory",
        lambda: fake_compare_extractors,
    )

    result = api_module.compare_extractors("<html><body>ok</body></html>", model="gpt-4o")

    assert recorded == [("<html><body>ok</body></html>", "gpt-4o")]
    assert result == {"fake": {"available": True, "model": "gpt-4o"}}


def test_ingest_use_case_default_fetcher_factory_is_composed_from_bootstrap(monkeypatch) -> None:
    calls: list[IngestConfig] = []

    class FakeFetcher:
        pass

    def fake_default_fetcher_factory(config: IngestConfig) -> object:
        calls.append(config)
        return FakeFetcher()

    monkeypatch.setattr(use_cases, "_default_fetcher_factory_impl", fake_default_fetcher_factory)

    use_case_factory = use_cases.IngestUseCase._default_fetcher_factory

    factory_result = use_case_factory(IngestConfig(timeout=15.0))

    assert isinstance(factory_result, FakeFetcher)
    assert len(calls) == 1
    assert calls[0].timeout == 15.0


def test_ingest_use_case_default_renderer_factory_is_composed_from_bootstrap(monkeypatch) -> None:
    calls: list[RenderConfig] = []

    class FakeRenderer:
        pass

    def fake_default_renderer_factory(config: RenderConfig) -> object:
        calls.append(config)
        return FakeRenderer()

    monkeypatch.setattr(use_cases, "_default_renderer_factory_impl", fake_default_renderer_factory)

    use_case_factory = use_cases.IngestUseCase._default_renderer_factory
    config = RenderConfig()

    returned = use_case_factory(config)

    assert isinstance(returned, FakeRenderer)
    assert len(calls) == 1
    assert calls[0] is config
