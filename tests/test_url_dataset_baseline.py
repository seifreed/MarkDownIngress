"""Opt-in live baseline test against the ada-url public URL dataset."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from markdown_ingress import IngestConfig, ingest_async, reset_ingest_stats

DATASET_URL = "https://raw.githubusercontent.com/ada-url/url-dataset/refs/heads/main/out.txt"


def _output_dir() -> Path:
    return Path("artifacts") / "url_dataset_baseline"


def _download_dataset() -> Path:
    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_file = output_dir / "out.txt"
    if not dataset_file.exists():
        urllib.request.urlretrieve(DATASET_URL, dataset_file)
    return dataset_file


def _warning_reasons(doc) -> list[str]:
    reasons: list[str] = []
    if doc.flags:
        reasons.append("flags")
    if doc.removed_elements and int(doc.removed_elements.get("hidden_elements", 0)) > 0:
        reasons.append("hidden_elements")
    if float(doc.injection_score) > 0:
        reasons.append("nonzero_injection_score")
    policy_action = doc.metadata.get("policy_action", "allow")
    if policy_action != "allow":
        reasons.append(f"policy_{policy_action}")
    return reasons


@pytest.mark.baseline
@pytest.mark.network
def test_url_dataset_baseline(pytestconfig):
    dataset_file = _download_dataset()
    all_urls = [
        line.strip()
        for line in dataset_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    raw_limit = str(pytestconfig.getoption("--url-baseline-limit"))
    limit = int(raw_limit)
    urls = all_urls if limit == 0 else all_urls[:limit]

    assert urls, "URL baseline dataset is empty"

    output_dir = _output_dir()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_dir / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    warnings_file = run_dir / "warnings.jsonl"
    errors_file = run_dir / "errors.jsonl"
    summary_file = run_dir / "summary.json"

    reset_ingest_stats()
    config = IngestConfig(
        mode="fast",
        timeout=5.0,
        extract_metadata=False,
        extract_links=False,
        policy_name="normal",
    )

    counters: Counter[str] = Counter()
    warning_types: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    write_lock = asyncio.Lock()
    count_lock = asyncio.Lock()

    async def write_jsonl(path: Path, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        async with write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    async def process_one(index: int, url: str) -> None:
        try:
            doc = await ingest_async(url, config=config)
            reasons = _warning_reasons(doc)
            async with count_lock:
                counters["processed"] += 1
                counters["ok"] += 1
                if reasons:
                    counters["warnings"] += 1
                    for reason in reasons:
                        warning_types[reason] += 1
            if reasons:
                await write_jsonl(
                    warnings_file,
                    {
                        "index": index,
                        "url": url,
                        "status": "warning",
                        "reasons": reasons,
                        "injection_score": doc.injection_score,
                        "flags": doc.flags,
                        "policy_action": doc.metadata.get("policy_action"),
                        "status_code": doc.metadata.get("status_code"),
                        "fetch_time_ms": doc.metadata.get("fetch_time_ms"),
                        "removed_elements": doc.removed_elements,
                    },
                )
        except Exception as exc:
            async with count_lock:
                counters["processed"] += 1
                counters["errors"] += 1
                error_types[type(exc).__name__] += 1
            await write_jsonl(
                errors_file,
                {
                    "index": index,
                    "url": url,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    async def run() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=64))
        batch_size = 512
        for start in range(0, len(urls), batch_size):
            batch = list(enumerate(urls[start : start + batch_size], start=start))
            await asyncio.gather(*(process_one(index, url) for index, url in batch))

    asyncio.run(run())

    summary = {
        "dataset_url": DATASET_URL,
        "dataset_file": str(dataset_file),
        "run_dir": str(run_dir),
        "requested_limit": limit,
        "processed": counters["processed"],
        "ok": counters["ok"],
        "warnings": counters["warnings"],
        "errors": counters["errors"],
        "error_types": dict(error_types),
        "warning_types": dict(warning_types),
        "warnings_file": str(warnings_file),
        "errors_file": str(errors_file),
    }
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    assert counters["processed"] == len(urls)
    assert counters["ok"] + counters["errors"] == len(urls)
    assert counters["ok"] > 0
