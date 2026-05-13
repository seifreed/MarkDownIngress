"""Stable identity and hash helpers for in-flight request deduplication."""

from __future__ import annotations

import hashlib
import json

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.plugin import fingerprint_plugin_directories
from markdown_ingress.core.ssrf import (
    normalize_domain_pattern,
    normalize_url_for_identity,
    resolve_allow_local_urls,
)


def build_request_identity(
    url: str,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
) -> dict[str, object]:
    """Build a stable identity payload for logically equivalent ingestions."""
    canonical_url = normalize_url_for_identity(url)
    allow_local_urls = resolve_allow_local_urls(config.allow_local_urls)
    key_payload: dict[str, object] = {
        "url": canonical_url,
        "mode": config.mode,
        "strict": config.strict,
        "allow_local_urls": allow_local_urls,
        "model": config.model,
        "timeout": config.timeout,
        "auto_render_threshold": config.auto_render_threshold,
        "stealth": config.stealth,
        "disable_http2": config.disable_http2,
        "extreme_mode": config.extreme_mode,
        "screenshot": config.screenshot,
        "extract_metadata": config.extract_metadata,
        "extract_links": config.extract_links,
        "advanced_security": config.advanced_security,
        "use_llm": config.use_llm,
        "policy_name": config.policy_name,
        "custom_patterns": list(config.custom_patterns),
        "plugin_dirs": fingerprint_plugin_directories(config.plugin_dirs),
        "output_profile": config.output_profile,
        "extract_blocks": config.extract_blocks,
        "chunking_strategy": config.chunking_strategy,
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "detect_language": config.detect_language,
        "normalize_multilingual": config.normalize_multilingual,
        "include_security_explanation": config.include_security_explanation,
        "include_observability": config.include_observability,
        "domain_request_interval": config.domain_request_interval,
        "circuit_breaker_threshold": config.circuit_breaker_threshold,
        "circuit_breaker_open_seconds": config.circuit_breaker_open_seconds,
        "render_cost_budget": config.render_cost_budget,
        "output_formats": list(config.output_formats),
    }
    key_payload["fetcher_user_agent"] = getattr(config, "fetcher_user_agent", None) or ""
    if matched_domain_policy is not None:
        key_payload["matched_domain_policy"] = {
            "domain": normalize_domain_pattern(matched_domain_policy.domain),
            "include_subdomains": matched_domain_policy.include_subdomains,
            "mode": matched_domain_policy.mode,
            "timeout": matched_domain_policy.timeout,
            "auto_render_threshold": matched_domain_policy.auto_render_threshold,
            "strict": matched_domain_policy.strict,
            "policy_name": matched_domain_policy.policy_name,
            "block_threshold": matched_domain_policy.block_threshold,
            "warn_threshold": matched_domain_policy.warn_threshold,
            "request_interval": matched_domain_policy.request_interval,
            "render_cost_budget": matched_domain_policy.render_cost_budget,
            "extract_metadata": matched_domain_policy.extract_metadata,
            "extract_links": matched_domain_policy.extract_links,
            "output_profile": matched_domain_policy.output_profile,
            "allowed_tags": list(matched_domain_policy.allowed_tags or []),
            "blocked_tags": list(matched_domain_policy.blocked_tags or []),
            "blocked_selectors": list(matched_domain_policy.blocked_selectors or []),
            "unwrap_selectors": list(matched_domain_policy.unwrap_selectors or []),
            "notes": matched_domain_policy.notes,
        }
    return key_payload


def make_request_key(
    url: str,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
) -> str:
    """Build a stable deduplication key for logically equivalent ingestions."""
    key_payload = build_request_identity(url, config, matched_domain_policy)
    serialized = json.dumps(key_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
