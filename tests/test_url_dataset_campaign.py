"""Opt-in large-scale campaign test against the ada-url public URL dataset."""

from __future__ import annotations

import pytest

from tests.url_dataset_campaign import run_campaign, select_scenarios


@pytest.mark.baseline
@pytest.mark.network
@pytest.mark.campaign
def test_url_dataset_campaign(pytestconfig):
    scenario_names = str(pytestconfig.getoption("--url-campaign-scenarios")).strip()
    scenarios = select_scenarios(scenario_names or None)
    total_limit = int(str(pytestconfig.getoption("--url-campaign-limit")))
    concurrency = int(str(pytestconfig.getoption("--url-campaign-concurrency")))
    batch_size = int(str(pytestconfig.getoption("--url-campaign-batch-size")))
    resume_run_dir = str(pytestconfig.getoption("--url-campaign-resume-dir")).strip() or None

    summary = run_campaign(
        total_limit=total_limit,
        scenarios=scenarios,
        concurrency=concurrency,
        batch_size=batch_size,
        resume_run_dir=resume_run_dir,
    )

    assert summary["selected_unique_urls"] >= total_limit
    assert summary["counts"]["processed"] == total_limit
    assert summary["counts"]["ok"] + summary["counts"]["errors"] == total_limit
    assert summary["counts"]["ok"] > 0
