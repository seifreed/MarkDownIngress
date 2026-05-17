"""Policy-engine construction helpers for document ingestion."""

from __future__ import annotations

import copy

from markdown_ingress.config_models import DomainPolicy
from markdown_ingress.core.policy import PolicyEngine


def build_policy_engine(
    policy_name: str, matched_domain_policy: DomainPolicy | None
) -> PolicyEngine:
    """Create the policy engine, applying optional domain-level threshold overrides."""
    if matched_domain_policy is None:
        return PolicyEngine.from_name(policy_name)
    if (
        matched_domain_policy.block_threshold is None
        and matched_domain_policy.warn_threshold is None
    ):
        return PolicyEngine.from_name(policy_name)

    base = PolicyEngine.from_name(policy_name).policy
    custom = copy.deepcopy(base)
    if matched_domain_policy.block_threshold is not None:
        custom.block_threshold = matched_domain_policy.block_threshold
    if matched_domain_policy.warn_threshold is not None:
        custom.warn_threshold = matched_domain_policy.warn_threshold
    if custom.warn_threshold >= custom.block_threshold:
        if custom.block_threshold > 0.0:
            adjusted = custom.block_threshold - 0.01
            if adjusted < 0.0:
                adjusted = custom.block_threshold * 0.9
            if adjusted < 0.0:
                adjusted = 0.0
            custom.warn_threshold = adjusted
        else:
            custom.warn_threshold = 0.0
    return PolicyEngine(policy=custom)
