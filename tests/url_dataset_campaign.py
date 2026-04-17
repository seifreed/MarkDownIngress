"""Massive live URL campaign runner against the ada-url dataset."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from markdown_ingress import ingest_async
from markdown_ingress.adapters.rendering.playwright_renderer import PLAYWRIGHT_AVAILABLE
from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.fetcher import Fetcher, UnsupportedContentTypeError
from markdown_ingress.core.orchestrator import get_ingest_stats, reset_ingest_stats
from markdown_ingress.models import SafeDocument

DATASET_URL = "https://raw.githubusercontent.com/ada-url/url-dataset/refs/heads/main/out.txt"
REPLACEABLE_ERROR_CLASSES = {
    "ConnectError",
    "dataset_invalid_host",
    "dataset_non_html",
    "dataset_unsupported_protocol",
    "forbidden",
    "gone",
    "HTTPStatusError",
    "legal_block",
    "local_guardrail_circuit_breaker",
    "navigation_failed",
    "network_disconnected",
    "network_io_suspended",
    "not_found",
    "rate_limited",
    "render_unavailable",
    "server_error",
    "ssl",
    "timeout",
    "unauthorized",
}


@dataclass(frozen=True)
class CampaignScenario:
    """One reproducible ingestion scenario within the mass URL campaign."""

    name: str
    description: str
    weight: int
    config: IngestConfig
    requires_playwright: bool = False
    max_concurrency: int | None = None

    def is_enabled(self) -> bool:
        return not self.requires_playwright or PLAYWRIGHT_AVAILABLE


def output_dir() -> Path:
    return Path("artifacts") / "url_dataset_campaign"


def availability_pool_path() -> Path:
    path = output_dir() / "availability_pool.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def download_dataset() -> Path:
    root = output_dir()
    root.mkdir(parents=True, exist_ok=True)
    dataset_file = root / "out.txt"
    if not dataset_file.exists():
        urllib.request.urlretrieve(DATASET_URL, dataset_file)
    return dataset_file


def load_unique_urls(limit: int) -> list[str]:
    dataset_file = download_dataset()
    urls: list[str] = []
    seen: set[str] = set()
    for line in dataset_file.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if limit > 0 and len(urls) >= limit:
            break
    return urls


def filter_supported_urls(urls: list[str]) -> tuple[list[str], Counter[str]]:
    """Keep only URLs the ingestion pipeline is expected to handle."""
    supported: list[str] = []
    dropped: Counter[str] = Counter()
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            dropped[f"unsupported_scheme:{parsed.scheme or 'missing'}"] += 1
            continue
        if not parsed.netloc:
            dropped["missing_host"] += 1
            continue
        supported.append(url)
    return supported, dropped


def spread_urls_by_host(urls: list[str]) -> list[str]:
    """Reorder URLs to avoid long bursts against the same hostname."""
    buckets: dict[str, deque[str]] = defaultdict(deque)
    host_order: list[str] = []
    for url in urls:
        host = (urlsplit(url).hostname or "").lower()
        if host not in buckets:
            host_order.append(host)
        buckets[host].append(url)

    spread: list[str] = []
    active_hosts = deque(host_order)
    while active_hosts:
        host = active_hosts.popleft()
        bucket = buckets[host]
        if bucket:
            spread.append(bucket.popleft())
        if bucket:
            active_hosts.append(host)
    return spread


def diversify_urls_by_host(urls: list[str], limit: int, *, soft_cap: int = 12) -> list[str]:
    """Select a more representative URL slice by preventing a few hosts from dominating the run."""
    if limit <= 0 or len(urls) <= limit:
        return urls[:limit] if limit > 0 else list(urls)

    buckets: dict[str, deque[str]] = defaultdict(deque)
    host_order: list[str] = []
    for url in urls:
        host = (urlsplit(url).hostname or "").lower()
        if host not in buckets:
            host_order.append(host)
        buckets[host].append(url)

    selected: list[str] = []
    per_host_counts: Counter[str] = Counter()
    active_cap = max(1, soft_cap)

    while len(selected) < limit:
        added_in_round = False
        for host in host_order:
            bucket = buckets[host]
            if not bucket or per_host_counts[host] >= active_cap:
                continue
            selected.append(bucket.popleft())
            per_host_counts[host] += 1
            added_in_round = True
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break
        if not added_in_round:
            active_cap *= 2
            if all(not bucket for bucket in buckets.values()):
                break

    return selected[:limit]


def default_scenarios() -> list[CampaignScenario]:
    return [
        CampaignScenario(
            name="fast_default",
            description="Fast mode, strict policy, lean metadata disabled for throughput baseline.",
            weight=20,
            config=IngestConfig(
                mode="fast",
                strict=True,
                timeout=5.0,
                extract_metadata=False,
                extract_links=False,
                output_profile="default",
                domain_request_interval=0.0,
                circuit_breaker_threshold=25,
                circuit_breaker_open_seconds=5.0,
                include_observability=True,
            ),
            max_concurrency=32,
        ),
        CampaignScenario(
            name="auto_default",
            description="Auto mode for SPA detection with standard strict settings.",
            weight=12,
            config=IngestConfig(
                mode="auto",
                strict=True,
                timeout=8.0,
                extract_metadata=False,
                extract_links=False,
                output_profile="llm_safe",
                domain_request_interval=0.0,
                circuit_breaker_threshold=25,
                circuit_breaker_open_seconds=5.0,
                include_observability=True,
            ),
            max_concurrency=4,
        ),
        CampaignScenario(
            name="rag_chunkable",
            description="Structured output for chunkable RAG ingestion.",
            weight=8,
            config=IngestConfig(
                mode="fast",
                strict=True,
                timeout=6.0,
                output_profile="rag_chunkable",
                domain_request_interval=0.0,
                circuit_breaker_threshold=25,
                circuit_breaker_open_seconds=5.0,
                include_observability=True,
            ),
            max_concurrency=24,
        ),
        CampaignScenario(
            name="search_profile",
            description="Search-oriented fast profile with permissive policy and size chunking.",
            weight=6,
            config=IngestConfig(
                mode="fast",
                strict=False,
                timeout=6.0,
                output_profile="for_search",
                domain_request_interval=0.0,
                circuit_breaker_threshold=25,
                circuit_breaker_open_seconds=5.0,
                include_observability=True,
            ),
            max_concurrency=24,
        ),
        CampaignScenario(
            name="domain_policy_override",
            description="Fast ingestion using a per-domain policy override to exercise runtime policy resolution.",
            weight=3,
            config=IngestConfig(
                mode="fast",
                strict=True,
                timeout=8.0,
                extract_metadata=True,
                extract_links=True,
                extract_blocks=True,
                chunking_strategy="heading",
                domain_request_interval=0.0,
                circuit_breaker_threshold=25,
                circuit_breaker_open_seconds=5.0,
                include_observability=True,
            ),
            max_concurrency=8,
        ),
        CampaignScenario(
            name="render_archive",
            description="Render-heavy archival profile to exercise Playwright, screenshots disabled.",
            weight=2,
            config=IngestConfig(
                mode="render",
                strict=True,
                timeout=12.0,
                output_profile="for_archive",
                stealth=False,
                extreme_mode=False,
                domain_request_interval=0.0,
                circuit_breaker_threshold=25,
                circuit_breaker_open_seconds=5.0,
                include_observability=True,
            ),
            requires_playwright=True,
            max_concurrency=1,
        ),
    ]


def select_scenarios(names: str | None) -> list[CampaignScenario]:
    scenarios = {scenario.name: scenario for scenario in default_scenarios()}
    if not names:
        selected = list(scenarios.values())
    else:
        selected = []
        for raw_name in names.split(","):
            name = raw_name.strip()
            if not name:
                continue
            if name not in scenarios:
                raise ValueError(f"Unknown URL campaign scenario: {name}")
            selected.append(scenarios[name])
    enabled = [scenario for scenario in selected if scenario.is_enabled()]
    return enabled


def allocate_urls(urls: list[str], scenarios: list[CampaignScenario]) -> dict[str, list[str]]:
    if not scenarios:
        raise ValueError("No enabled scenarios selected for URL campaign")
    weighted_names: list[str] = []
    for scenario in scenarios:
        weighted_names.extend([scenario.name] * max(1, scenario.weight))
    allocation = {scenario.name: [] for scenario in scenarios}
    for index, url in enumerate(urls):
        scenario_name = weighted_names[index % len(weighted_names)]
        allocation[scenario_name].append(url)
    return allocation


def classify_warning(doc: SafeDocument) -> list[str]:
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
    if doc.metadata.get("status_code", 200) >= 400:
        reasons.append("http_status_warning")
    return reasons


def classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "circuit breaker open" in message:
        return "local_guardrail_circuit_breaker"
    if isinstance(exc, UnsupportedContentTypeError):
        return "dataset_non_html"
    if "download is starting" in message:
        return "dataset_non_html"
    if "err_name_not_resolved" in message:
        return "dataset_invalid_host"
    if "err_internet_disconnected" in message:
        return "network_disconnected"
    if "err_network_io_suspended" in message:
        return "network_io_suspended"
    if "err_connection_timed_out" in message:
        return "timeout"
    if "err_failed" in message and ".pdf" in message:
        return "dataset_non_html"
    if "err_failed" in message:
        return "navigation_failed"
    if "name or service not known" in message or "nodename nor servname" in message:
        return "dataset_invalid_host"
    if (
        "unsupportedprotocol" in type(exc).__name__.lower()
        or "request url is missing an 'http://' or 'https://'" in message
    ):
        return "dataset_unsupported_protocol"
    if "certificate" in message or "ssl" in message:
        return "ssl"
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "401" in message:
        return "unauthorized"
    if "429" in message:
        return "rate_limited"
    if "403" in message:
        return "forbidden"
    if "404" in message:
        return "not_found"
    if "410" in message:
        return "gone"
    if "451" in message:
        return "legal_block"
    if "500" in message or "502" in message or "503" in message or "504" in message:
        return "server_error"
    if "playwright" in message or type(exc).__name__ == "ImportError":
        return "render_unavailable"
    return type(exc).__name__


def _load_availability_pool() -> tuple[dict[str, str], Counter[str]]:
    statuses: dict[str, str] = {}
    path = availability_pool_path()
    if not path.exists():
        return statuses, Counter()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = payload.get("url")
            status = payload.get("status")
            if not isinstance(url, str) or not isinstance(status, str):
                continue
            statuses[url] = status
    counts = Counter(statuses.values())
    return statuses, counts


def _append_availability_records(records: list[dict[str, str]]) -> None:
    if not records:
        return
    path = availability_pool_path()
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


async def collect_available_urls(
    *,
    total_limit: int,
    concurrency: int,
    timeout: float = 5.0,
    progress_callback: Callable[[int, Counter[str], Counter[str], int], None] | None = None,
) -> tuple[list[str], Counter[str], Counter[str], int]:
    """Build a pool of URLs that are actually reachable and HTML-like before running scenarios."""
    source_urls = load_unique_urls(0)
    supported_urls, dropped_url_types = filter_supported_urls(source_urls)
    candidates = diversify_urls_by_host(spread_urls_by_host(supported_urls), len(supported_urls))
    cached_statuses, cached_status_counts = _load_availability_pool()

    available_from_cache = [url for url in candidates if cached_statuses.get(url) == "available"]
    if progress_callback is not None:
        progress_callback(
            availability_checked=len(cached_statuses),
            availability_error_types=Counter(cached_status_counts),
            dropped_url_types=dropped_url_types,
            selected_count=min(len(available_from_cache), total_limit),
        )
    if len(available_from_cache) >= total_limit:
        return (
            available_from_cache[:total_limit],
            dropped_url_types,
            cached_status_counts,
            len(cached_statuses),
        )

    available: list[str] = list(available_from_cache)
    availability_error_types: Counter[str] = Counter(cached_status_counts)
    checked = len(cached_statuses)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 32)))
    fetcher = Fetcher(
        timeout=timeout,
        domain_request_interval=0.0,
        circuit_breaker_threshold=50,
        circuit_breaker_open_seconds=2.0,
    )
    unchecked = [url for url in candidates if url not in cached_statuses]

    async def check_url(url: str) -> tuple[str, str | None]:
        async with semaphore:
            try:
                await asyncio.to_thread(fetcher.fetch_sync, url)
                return url, None
            except Exception as exc:  # noqa: BLE001
                return url, classify_error(exc)

    for start in range(0, len(unchecked), max(32, concurrency * 2)):
        batch = unchecked[start : start + max(32, concurrency * 2)]
        results = await asyncio.gather(*(check_url(url) for url in batch))
        records: list[dict[str, str]] = []
        for url, error_class in results:
            checked += 1
            if error_class is None:
                available.append(url)
                availability_error_types["available"] += 1
                records.append({"url": url, "status": "available"})
            else:
                availability_error_types[error_class] += 1
                records.append({"url": url, "status": error_class})
        _append_availability_records(records)
        if progress_callback is not None:
            progress_callback(
                availability_checked=checked,
                availability_error_types=Counter(availability_error_types),
                dropped_url_types=dropped_url_types,
                selected_count=min(len(available), total_limit),
            )
        if len(available) >= total_limit:
            break

    return available[:total_limit], dropped_url_types, availability_error_types, checked


def _scenario_output_dir(run_dir: Path, scenario: CampaignScenario) -> Path:
    path = run_dir / scenario.name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_domain_override(url: str) -> DomainPolicy | None:
    hostname = urlsplit(url).hostname
    if not hostname:
        return None
    return DomainPolicy(
        domain=hostname,
        include_subdomains=False,
        output_profile="for_search",
        request_interval=0.0,
        notes="campaign_override",
    )


def _scenario_config_for_url(scenario: CampaignScenario, url: str) -> IngestConfig:
    config = scenario.config.clone()
    if scenario.name == "domain_policy_override":
        override = _make_domain_override(url)
        if override is not None:
            config.domain_policies = [override]
    return config


def _scenario_summary_config(scenario: CampaignScenario) -> dict[str, Any]:
    config = scenario.config.apply_output_profile()
    payload = {key: value for key, value in asdict(config).items() if not key.startswith("_")}
    if scenario.name == "domain_policy_override":
        payload["domain_policies"] = ["dynamic_per_url"]
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _counts_payload(counter: Counter[str]) -> dict[str, int]:
    return {
        "processed": int(counter.get("processed", 0)),
        "ok": int(counter.get("ok", 0)),
        "errors": int(counter.get("errors", 0)),
        "warnings": int(counter.get("warnings", 0)),
    }


class JsonlSink:
    """Persistent JSONL writer to avoid reopening files per event."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._lock = asyncio.Lock()

    async def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        async with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def run_campaign(
    *,
    total_limit: int,
    scenarios: list[CampaignScenario],
    concurrency: int,
    batch_size: int,
    resume_run_dir: str | None = None,
) -> dict[str, Any]:
    if resume_run_dir:
        run_dir = Path(resume_run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = output_dir() / f"campaign_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
    progress_file = run_dir / "progress.json"

    def write_preselection_progress(
        *,
        availability_checked: int,
        availability_error_types: Counter[str],
        dropped_url_types: Counter[str],
        selected_count: int,
    ) -> None:
        payload = {
            "run_dir": str(run_dir),
            "timestamp": datetime.now(UTC).isoformat(),
            "current_scenario": "availability_pool",
            "completed_in_scenario": selected_count,
            "total_in_scenario": total_limit,
            "campaign_counts": {"processed": 0, "ok": 0, "errors": 0, "warnings": 0},
            "campaign_error_types": {},
            "campaign_warning_types": {},
            "dropped_url_types": dict(dropped_url_types),
            "availability_error_types": dict(availability_error_types),
            "availability_checked": availability_checked,
        }
        _write_json(progress_file, payload)

    urls, dropped_url_types, availability_error_types, availability_checked = asyncio.run(
        collect_available_urls(
            total_limit=total_limit,
            concurrency=concurrency,
            progress_callback=write_preselection_progress,
        )
    )
    if len(urls) < total_limit:
        raise AssertionError(
            f"Requested {total_limit} available URLs but only found {len(urls)} after checking {availability_checked}"
        )

    allocation = allocate_urls(urls, scenarios)

    reset_ingest_stats()
    count_lock = asyncio.Lock()
    campaign_counts: Counter[str] = Counter()
    campaign_error_types: Counter[str] = Counter()
    campaign_warning_types: Counter[str] = Counter()
    scenario_summaries: dict[str, dict[str, Any]] = {}

    def rebuild_campaign_counters() -> None:
        campaign_counts.clear()
        campaign_error_types.clear()
        campaign_warning_types.clear()
        for summary in scenario_summaries.values():
            counts = summary.get("counts", {})
            campaign_counts["processed"] += int(counts.get("processed", 0))
            campaign_counts["ok"] += int(counts.get("ok", 0))
            campaign_counts["errors"] += int(counts.get("errors", 0))
            campaign_counts["warnings"] += int(counts.get("warnings", 0))
            for key, value in summary.get("error_types", {}).items():
                campaign_error_types[key] += int(value)
            for key, value in summary.get("warning_types", {}).items():
                campaign_warning_types[key] += int(value)

    def write_progress(
        current_scenario: str, completed_in_scenario: int, total_in_scenario: int
    ) -> None:
        payload = {
            "run_dir": str(run_dir),
            "timestamp": datetime.now(UTC).isoformat(),
            "current_scenario": current_scenario,
            "completed_in_scenario": completed_in_scenario,
            "total_in_scenario": total_in_scenario,
            "campaign_counts": _counts_payload(campaign_counts),
            "campaign_error_types": dict(campaign_error_types),
            "campaign_warning_types": dict(campaign_warning_types),
            "dropped_url_types": dict(dropped_url_types),
            "availability_error_types": dict(availability_error_types),
            "availability_checked": availability_checked,
        }
        _write_json(progress_file, payload)

    def completed_indexes(path: Path) -> set[int]:
        indexes: set[int] = set()
        if not path.exists():
            return indexes
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                index = payload.get("index")
                if isinstance(index, int):
                    indexes.add(index)
        return indexes

    def load_existing_scenario_summary(scenario: CampaignScenario) -> dict[str, Any] | None:
        summary_path = _scenario_output_dir(run_dir, scenario) / "summary.json"
        if not summary_path.exists():
            return None
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    async def run_scenario(scenario: CampaignScenario, urls_for_scenario: list[str]) -> None:
        scenario_dir = _scenario_output_dir(run_dir, scenario)
        warnings_file = scenario_dir / "warnings.jsonl"
        errors_file = scenario_dir / "errors.jsonl"
        warning_sink = JsonlSink(warnings_file)
        error_sink = JsonlSink(errors_file)
        scenario_counts: Counter[str] = Counter()
        warning_types: Counter[str] = Counter()
        error_types: Counter[str] = Counter()
        completed = completed_indexes(warnings_file) | completed_indexes(errors_file)
        pending = [
            (index, url) for index, url in enumerate(urls_for_scenario) if index not in completed
        ]
        scenario_concurrency = max(
            1, min(concurrency, scenario.max_concurrency or concurrency, batch_size)
        )
        semaphore = asyncio.Semaphore(scenario_concurrency)

        existing_summary = load_existing_scenario_summary(scenario)
        if existing_summary is not None:
            counts = existing_summary.get("counts", {})
            scenario_counts.update({key: int(value) for key, value in counts.items()})
            warning_types.update(
                {
                    key: int(value)
                    for key, value in existing_summary.get("warning_types", {}).items()
                }
            )
            error_types.update(
                {key: int(value) for key, value in existing_summary.get("error_types", {}).items()}
            )
        else:
            scenario_counts["processed"] = len(completed)

        async def process_wrapped(index: int, url: str) -> None:
            async with semaphore:
                warnings_sink = warning_sink
                errors_sink = error_sink
                config = _scenario_config_for_url(scenario, url)
                try:
                    doc = await ingest_async(url, config=config)
                    reasons = classify_warning(doc)
                    async with count_lock:
                        scenario_counts["processed"] += 1
                        scenario_counts["ok"] += 1
                        campaign_counts["processed"] += 1
                        campaign_counts["ok"] += 1
                        if reasons:
                            scenario_counts["warnings"] += 1
                            campaign_counts["warnings"] += 1
                            for reason in reasons:
                                warning_types[reason] += 1
                                campaign_warning_types[reason] += 1
                    if reasons:
                        await warnings_sink.write(
                            {
                                "index": index,
                                "url": url,
                                "scenario": scenario.name,
                                "status": "warning",
                                "reasons": reasons,
                                "injection_score": doc.injection_score,
                                "flags": doc.flags,
                                "policy_action": doc.metadata.get("policy_action"),
                                "mode": doc.metadata.get("mode"),
                                "status_code": doc.metadata.get("status_code"),
                                "fetch_time_ms": doc.metadata.get("fetch_time_ms"),
                                "token_estimate": doc.token_estimate,
                                "removed_elements": doc.removed_elements,
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    error_class = classify_error(exc)
                    async with count_lock:
                        scenario_counts["processed"] += 1
                        scenario_counts["errors"] += 1
                        campaign_counts["processed"] += 1
                        campaign_counts["errors"] += 1
                        error_types[error_class] += 1
                        campaign_error_types[error_class] += 1
                    await errors_sink.write(
                        {
                            "index": index,
                            "url": url,
                            "scenario": scenario.name,
                            "status": "error",
                            "error_type": type(exc).__name__,
                            "error_class": error_class,
                            "error": str(exc),
                            "config": {
                                "mode": config.mode,
                                "strict": config.strict,
                                "timeout": config.timeout,
                                "output_profile": config.output_profile,
                                "extract_metadata": config.extract_metadata,
                                "extract_links": config.extract_links,
                                "extract_blocks": config.extract_blocks,
                                "chunking_strategy": config.chunking_strategy,
                                "include_observability": config.include_observability,
                                "domain_policies": [
                                    policy.domain for policy in config.domain_policies
                                ],
                            },
                        }
                    )

        try:
            for start in range(0, len(pending), batch_size):
                batch = pending[start : start + batch_size]
                await asyncio.gather(*(process_wrapped(index, url) for index, url in batch))
                completed_now = len(completed) + min(start + len(batch), len(pending))
                write_progress(scenario.name, completed_now, len(urls_for_scenario))
                print(
                    f"[url-campaign] scenario={scenario.name} completed={completed_now}/{len(urls_for_scenario)} "
                    f"scenario_concurrency={scenario_concurrency} campaign_processed={campaign_counts['processed']} "
                    f"errors={campaign_counts['errors']} timeouts={campaign_error_types.get('timeout', 0)}",
                    flush=True,
                )
            summary = {
                "scenario": scenario.name,
                "description": scenario.description,
                "assigned_urls": len(urls_for_scenario),
                "config": _scenario_summary_config(scenario),
                "counts": _counts_payload(scenario_counts),
                "warning_types": dict(warning_types),
                "error_types": dict(error_types),
                "resumed_completed_indexes": len(completed),
                "scenario_concurrency": scenario_concurrency,
                "warnings_file": str(warnings_file),
                "errors_file": str(errors_file),
            }
            _write_json(scenario_dir / "summary.json", summary)
            scenario_summaries[scenario.name] = summary
            rebuild_campaign_counters()
        finally:
            warning_sink.close()
            error_sink.close()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=concurrency))
        for scenario in scenarios:
            existing_summary = load_existing_scenario_summary(scenario)
            if existing_summary is not None:
                scenario_summaries[scenario.name] = existing_summary
            rebuild_campaign_counters()
            await run_scenario(scenario, allocation[scenario.name])

    asyncio.run(run())

    summary = {
        "dataset_url": DATASET_URL,
        "dataset_file": str(download_dataset()),
        "run_dir": str(run_dir),
        "requested_unique_urls": total_limit,
        "selected_unique_urls": len(urls),
        "availability_checked_urls": availability_checked,
        "dropped_url_types": dict(dropped_url_types),
        "availability_error_types": dict(availability_error_types),
        "enabled_scenarios": [scenario.name for scenario in scenarios],
        "concurrency": concurrency,
        "batch_size": batch_size,
        "counts": _counts_payload(campaign_counts),
        "warning_types": dict(campaign_warning_types),
        "error_types": dict(campaign_error_types),
        "scenario_allocations": {
            name: len(urls_for_scenario) for name, urls_for_scenario in allocation.items()
        },
        "scenario_summaries": scenario_summaries,
        "ingest_stats": get_ingest_stats(),
    }
    _write_json(run_dir / "summary.json", summary)
    return summary
