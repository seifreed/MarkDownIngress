"""Tests for pytest collection controls."""

from __future__ import annotations

from typing import Any

from tests import conftest as test_config


class FakeConfig:
    def __init__(self, options: dict[str, bool]):
        self.options = options

    def getoption(self, name: str) -> bool:
        return self.options.get(name, False)


class FakeItem:
    def __init__(self, keywords: dict[str, bool]):
        self.keywords = keywords
        self.markers: list[Any] = []

    def add_marker(self, marker: Any) -> None:
        self.markers.append(marker)


def test_url_baseline_flag_does_not_enable_campaign(monkeypatch):
    monkeypatch.delenv("MDI_RUN_URL_BASELINE", raising=False)
    monkeypatch.delenv("MDI_RUN_URL_CAMPAIGN", raising=False)
    config = FakeConfig({"--run-url-baseline": True, "--run-url-campaign": False})
    baseline_item = FakeItem({"baseline": True})
    campaign_item = FakeItem({"baseline": True, "campaign": True})

    test_config.pytest_collection_modifyitems(config, [baseline_item, campaign_item])

    assert baseline_item.markers == []
    assert len(campaign_item.markers) == 1


def test_url_campaign_env_does_not_enable_plain_baseline(monkeypatch):
    monkeypatch.delenv("MDI_RUN_URL_BASELINE", raising=False)
    monkeypatch.setenv("MDI_RUN_URL_CAMPAIGN", "1")
    config = FakeConfig({"--run-url-baseline": False, "--run-url-campaign": False})
    baseline_item = FakeItem({"baseline": True})
    campaign_item = FakeItem({"baseline": True, "campaign": True})

    test_config.pytest_collection_modifyitems(config, [baseline_item, campaign_item])

    assert len(baseline_item.markers) == 1
    assert campaign_item.markers == []
